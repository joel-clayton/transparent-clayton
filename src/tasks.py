import json
import logging

from celery import chain
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


@app.task
def cc_meeting_workflow() -> None:
    workflow.apply_async()


workflow = chain(
    get_cc_meeting_details_for_download.si(),
    download_cc_meeting_video.si(),
    compress_cc_meeting_video.si(),
    upload_cc_meeting_video.si(),
    extract_cc_meeting_audio.si(),
    transcribe_cc_meeting_audio.si(),
    upload_cc_meeting_transcript.si(),
)

app.conf.beat_schedule = {
    "cc-meeting-workflow": {
        "task": "src.tasks.cc_meeting_workflow",
        "schedule": crontab(hour=21, minute=0),
    },
}
