# Install the requests package by executing the command "pip install requests"

import time
from secrets import assembly_ai_auth_key

import requests

from src.processors.process import Processor

base_url = "https://api.assemblyai.com"
headers = {"authorization": assembly_ai_auth_key}


class Transcriber(Processor):
    def process_for_date(self, input_filepath: str, output_filepath: str) -> None:
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
                    u.write(f"Speaker {utterance['speaker']}:\n{utterance['text']}\n\n")
                u.close()
                break

            elif transcription_result["status"] == "error":
                raise RuntimeError(
                    f"Transcription failed: {transcription_result['error']}"
                )

            else:
                time.sleep(30)
