import json
import logging

from celery import chain
from celery.exceptions import Ignore
from celery.schedules import crontab

from celery_app import app, r
from src.constants import SCRAPED_CC_MTG_KEY, EXITED_EARLY
from src.processors.compress import Compressor
from src.processors.download import Downloader
from src.processors.extract import Extractor
from src.processors.transcribe import Transcriber
from src.processors.update_wiki import WikiUpdater
from src.scrapers.cc_meetings import get_latest_downloaded_date, parse_meetings_from_url
from src.processors.upload_transcript import TranscriptUploader
from src.processors.upload_video import VideoUploader
from src.util import get_datetime_from_string, send_to_discord_bots

logger = logging.getLogger(__name__)


@app.task()
def get_cc_meeting_details_for_download() -> None:
    latest_date_str = get_latest_downloaded_date()
    logger.info(f"latest date str: {latest_date_str}")
    if not latest_date_str:
        logger.warning("Did not find any downloaded City Council meetings in storage.")
        return
    try:
        latest_date = get_datetime_from_string(latest_date_str)
        if latest_date is None:
            raise Exception(
                f"Could not parse a date from {latest_date_str!r}; skipping"
            )
        meetings_to_process = parse_meetings_from_url(latest_date)
        if not meetings_to_process:
            raise Ignore(EXITED_EARLY)
        meeting_dates = sorted(
            [
                m["key"]
                for m in meetings_to_process
                if (parsed := get_datetime_from_string(m["key"])) is not None
                and parsed > latest_date
            ]
        )
        r.set(SCRAPED_CC_MTG_KEY, json.dumps(meeting_dates))
        logger.info(f"meetings_to_process: {meetings_to_process}")
        return
    except Exception as e:
        log_error(None, e, e.__traceback__)
        raise Ignore(f"Something has gone pear-shaped: {e}")


@app.task
def download_cc_meeting_video() -> None:
    downloader = Downloader()
    downloader.process()


@app.task
def compress_cc_meeting_video() -> None:
    compressor = Compressor()
    compressor.process()


@app.task
def upload_cc_meeting_video() -> None:
    video_uploader = VideoUploader()
    video_uploader.process()


@app.task
def extract_cc_meeting_audio() -> None:
    extractor = Extractor()
    extractor.process()


@app.task
def transcribe_cc_meeting_audio() -> None:
    transcriber = Transcriber()
    transcriber.process()


@app.task
def upload_cc_meeting_transcript() -> None:
    transcript_uploader = TranscriptUploader()
    transcript_uploader.process()


@app.task
def update_cc_mtg_wiki() -> None:
    wiki_updater = WikiUpdater()
    wiki_updater.process()


@app.task
def notify_success() -> None:
    logger.info("Completed processing 🎉")


@app.task
def cc_meeting_workflow() -> None:
    workflow.apply_async()


@app.task
def log_error(request: object, exc: BaseException, traceback: object) -> None:
    for message in (
        f"REQUEST: {request}",
        f"EXCEPTION: {exc}",
        f"TRACEBACK: {traceback}",
    ):
        if EXITED_EARLY not in exc.args:
            send_to_discord_bots(message)
            return
    logger.info("No new meetings to process, exiting")


workflow = chain(
    get_cc_meeting_details_for_download.si().on_error(log_error.s()),
    download_cc_meeting_video.si().on_error(log_error.s()),
    compress_cc_meeting_video.si().on_error(log_error.s()),
    upload_cc_meeting_video.si().on_error(log_error.s()),
    extract_cc_meeting_audio.si().on_error(log_error.s()),
    transcribe_cc_meeting_audio.si().on_error(log_error.s()),
    upload_cc_meeting_transcript.si().on_error(log_error.s()),
    update_cc_mtg_wiki.si().on_error(log_error.s()),
    notify_success.si().on_error(log_error.s()),
)


app.conf.beat_schedule = {
    "cc-meeting-workflow": {
        "task": "src.tasks.cc_meeting_workflow",
        "schedule": crontab(hour="*,8-18", minute=0),
    },
}
