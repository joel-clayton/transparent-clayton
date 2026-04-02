import json
import logging

from celery.schedules import crontab

from celery_app import app, r
from src.constants import (
    SCRAPED_CC_MTG_KEY,
)
from src.processors.compress import Compressor
from src.processors.download import Downloader
from src.processors.extract import Extractor
from src.processors.transcribe import Transcriber
from src.scrapers.cc_meetings import get_latest_downloaded_date, parse_meetings_from_url
from src.processors.upload_transcript import TranscriptUploader
from src.processors.upload_video import VideoUploader

logger = logging.getLogger(__name__)


@app.task
def get_cc_meeting_details_for_download() -> None:
    latest_date = get_latest_downloaded_date()
    if not latest_date:
        logger.warning("Did not find any downloaded City Council meetings in storage.")
        return
    meetings_to_process = parse_meetings_from_url(latest_date)
    meeting_dates = sorted(
        [key for key in meetings_to_process.keys() if key > latest_date]
    )
    r.set(SCRAPED_CC_MTG_KEY, json.dumps(meeting_dates))


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


app.conf.beat_schedule = {
    "get-cc-meeting-details-to-process": {
        "task": "src.tasks.get_cc_meeting_details_for_download",
        "schedule": crontab(hour=18, minute=0),
    },
    "download-cc-meeting-video": {
        "task": "src.tasks.download_unprocessed_video",
        "schedule": crontab(hour=19, minute=0),
    },
    "compress-cc-meeting-video": {
        "task": "src.tasks.compress_cc_meeting_video",
        "schedule": crontab(hour=20, minute=0),
    },
    "upload-cc-meeting-video": {
        "task": "src.tasks.upload_cc_meeting_video",
        "schedule": crontab(hour=22, minute=0),
    },
    "extract-cc-meeting-audio": {
        "task": "src.tasks.extract_cc_meeting_audio",
        "schedule": crontab(hour=23, minute=0),
    },
    "transcribe-cc-meeting-audio": {
        "task": "src.tasks.transcribe_cc_meeting_audio",
        "schedule": crontab(hour=0, minute=0),
    },
    "upload-cc-meeting-transcript": {
        "task": "src.tasks.upload_cc_meeting_transcript",
        "schedule": crontab(hour=1, minute=0),
    },
}
