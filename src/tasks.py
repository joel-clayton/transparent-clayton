import json

from celery.schedules import crontab

from celery_app import app, r
from src.processors.transcriber import Transcriber
from src.uploaders.transcript_uploader import TranscriptUploader
from src.uploaders.video_uploader import VideoUploader
from src.constants import SCRAPED_CC_MTG_KEY, DOWNLOADED_CC_MTG_KEY, TRANSCRIPT_UPLOADED_CC_MTG_KEY, \
    COMPRESSED_CC_MTG_KEY
from src.processors.download import (Downloader)
from src.processors.compress import Compressor
from src.scrapers.cc_meetings import get_latest_downloaded_date, \
    parse_meetings_from_url
from src.settings import DOWNLOADED_DIR, COMPRESSED_DIR, EXTRACTED_AUDIO_DIR, TRANSCRIBED_DIR
from src.types import JobType, SourceType


@app.task
def get_cc_meeting_details_for_download() -> None:
    latest_date = get_latest_downloaded_date()
    meetings_to_process = parse_meetings_from_url(latest_date)
    meeting_dates = sorted(
        [key for key in meetings_to_process.keys() if key > latest_date]
    )
    r.set(SCRAPED_CC_MTG_KEY, json.dumps(meeting_dates))

@app.task
def download_cc_meeting_video() -> None:
    downloader = Downloader(
        input_dir=None,
        output_dir=DOWNLOADED_DIR,
        job_type=JobType.DOWNLOAD,
        source_type=SourceType.CITY_COUNCIL_MEETING,
        redis_key=DOWNLOADED_CC_MTG_KEY,
    )
    downloader.process()

@app.task
def compress_cc_meeting_video() -> None:
    compressor = Compressor(
        input_dir=DOWNLOADED_DIR,
        output_dir=COMPRESSED_DIR,
        job_type=JobType.COMPRESS,
        source_type=SourceType.CITY_COUNCIL_MEETING,
        redis_key=COMPRESSED_CC_MTG_KEY,
    )
    compressor.process()


@app.task
def upload_cc_meeting_video() -> None:
    video_uploader = VideoUploader(source_dir=COMPRESSED_DIR)
    video_uploader.upload()


@app.task
def extract_cc_meeting_audio() -> None:
    pass

@app.task
def transcribe_cc_meeting_audio() -> None:
    transcriber = Transcriber(
        input_dir=EXTRACTED_AUDIO_DIR,
        output_dir=TRANSCRIBED_DIR,
        job_type=JobType.TRANSCRIBE_AUDIO,
        source_type=SourceType.CITY_COUNCIL_MEETING,
        redis_key=TRANSCRIPT_UPLOADED_CC_MTG_KEY
    )
    transcriber.process()


@app.task
def upload_cc_meeting_transcript() -> None:
    transcript_uploader = TranscriptUploader(source_dir=EXTRACTED_AUDIO_DIR)
    transcript_uploader.upload()


app.conf.beat_schedule = {
    'get-cc-meeting-details-to-process': {
        'task': 'src.tasks.get_cc_meeting_details_for_download',
        'schedule': crontab(hour=23, minute=30),
    },
    'download-cc-meeting-video': {
        'task': 'src.tasks.download_unprocessed_video',
        'schedule': crontab(hour=23, minute=45),
    },
    'compress-cc-meeting-video': {
        'task': 'src.tasks.compress_cc_meeting_video',
        'schedule': crontab(hour=1, minute=30),
    },
    'upload-cc-meeting-video': {
        'task': 'src.tasks.upload_cc_meeting_video',
        'schedule': crontab(hour=1, minute=30),
    },
    'extract-cc-meeting-audio': {
        'task': 'src.tasks.extract_cc_meeting_audio',
        'schedule': None,
    },
    'transcribe-cc-meeting-audio': {
        'task': 'src.tasks.transcribe_cc_meeting_audio',
        'schedule': crontab(hour=1, minute=30),
    },
    'upload-cc-meeting-transcript': {
        'task': 'src.tasks.upload_cc_meeting_transcript',
        'schedule': crontab(hour=1, minute=30),
    },
}