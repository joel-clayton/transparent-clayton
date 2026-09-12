import time
from datetime import datetime, timedelta
from typing import List

import requests

from celery_app import r
from src.constants import NO_AUDIO_KEY, TRANSCRIPT_UPLOADED_KEY
from src.processors.process import Processor
from src.settings import EXTRACTED_AUDIO_DIR, TRANSCRIBED_DIR
from src.types import SourceType, JobType, MEETING_TYPE_BY_SOURCE
from src.secrets import ASSEMBLY_AI_AUTH_KEY

base_url = "https://api.assemblyai.com"
headers = {"authorization": ASSEMBLY_AI_AUTH_KEY}

# AssemblyAI errors that mean "this recording has nothing to transcribe" rather
# than a transient failure — a silent/no-speech video (some GHAD/committee and
# "General" recordings). We record these and move on instead of blocking.
NO_AUDIO_ERROR_MARKERS = ("no spoken audio", "does not contain audio", "no audio")


def _is_no_audio_error(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in NO_AUDIO_ERROR_MARKERS)


class Transcriber(Processor):
    def __init__(
        self, source_type: SourceType = SourceType.CITY_COUNCIL_MEETING
    ) -> None:
        self.input_job_type = JobType.EXTRACT_AUDIO
        self.job_type = JobType.TRANSCRIBE_AUDIO
        self.source_type = source_type
        self.meeting_type = MEETING_TYPE_BY_SOURCE[source_type]
        self.redis_key = self.meeting_type.redis_key(TRANSCRIPT_UPLOADED_KEY)
        super().__init__()

    def _no_audio_dates(self) -> set[str]:
        """Meeting keys recorded as having no spoken audio (never re-attempted)."""
        return {
            m.decode("utf-8") if isinstance(m, bytes) else m
            for m in (r.smembers(self.meeting_type.redis_key(NO_AUDIO_KEY)) or set())
        }

    def gather_input_dates(self) -> List:
        return self.gather_dates(EXTRACTED_AUDIO_DIR)

    def gather_output_dates(self) -> List:
        # A no-spoken-audio meeting produces no transcript file; treat it as
        # done (via the recorded set) so it isn't retried on every run.
        return sorted(set(self.gather_dates(TRANSCRIBED_DIR)) | self._no_audio_dates())

    def process_for_date(self, date: str) -> None:
        dt = self.extract_datetime_object(date)

        if dt and dt <= datetime(2025, 11, 1):
            return None
        elif not dt:
            raise Exception(f"Could not find a datetime in {date} for {self.job_type}")

        input_filepath = self.construct_filepath_for_date(date, self.input_job_type)
        output_filepath = self.construct_filepath_for_date(date)

        with open(input_filepath, "rb") as f:
            response = requests.post(base_url + "/v2/upload", headers=headers, data=f)

        audio_url = response.json()["upload_url"]

        data = {
            "audio_url": audio_url,
            "language_detection": True,
            # Uses universal-3-pro for en, es, de, fr, it, pt. Else uses
            # universal-2 for support across all other languages
            "speech_models": ["universal-3-pro", "universal-2"],
            "speaker_labels": True,
        }

        url = base_url + "/v2/transcript"
        response = requests.post(url, json=data, headers=headers)

        transcript_id = response.json()["id"]
        polling_endpoint = base_url + "/v2/transcript/" + transcript_id

        while True:
            transcription_result = requests.get(
                polling_endpoint, headers=headers
            ).json()

            if transcription_result["status"] == "completed":
                u = open(output_filepath, "w")
                for utterance in transcription_result["utterances"]:
                    u.write(
                        f"[Speaker {utterance['speaker']}] "
                        f"({str(timedelta(seconds=int(utterance['start'] / 1000)))} - {str(timedelta(seconds=int(utterance['end'] / 1000)))})\n"
                        f"{utterance['text']}\n\n"
                    )
                u.close()
                break

            elif transcription_result["status"] == "error":
                error = transcription_result["error"]
                # A recording with no speech isn't a pipeline failure: record it
                # so it's never retried and downstream can still list the video,
                # and stop blocking every later meeting in the batch.
                if _is_no_audio_error(error):
                    r.sadd(self.meeting_type.redis_key(NO_AUDIO_KEY), date)
                    self.logger.warning(
                        "No spoken audio for %s; recording as untranscribed", date
                    )
                    return None
                raise RuntimeError(f"Transcription failed: {error}")

            else:
                time.sleep(30)

        self.log_complete_for_date(date=date)
