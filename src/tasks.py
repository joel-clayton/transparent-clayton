import json
import os

from celery.schedules import crontab

from celery_app import app, r
from src.av.rip_audio import Ripper
from src.constants import SCRAPED_CC_MTG_KEY, DOWNLOADED_CC_MTG_KEY, DETAIL_CC_MTG_KEY, \
    DEFAULT_ONE_WEEK_SECONDS_EXPIRATION, DOWNLOADED_DIR, COMPRESSED_DIR, AUDIO_DIR, COMPRESSION_OPTIONS, \
    COMPRESSION_PATTERN
from src.av.download import (OUTFILE_LOCATION, OUTFILE_NAME, get_m3u_url,
                             get_media_stream, logger)
from src.av.compress import Compressor
from src.scrapers.cc_meetings.cc_meetings import get_latest_downloaded_date, \
    parse_meetings_from_url


@app.task
def get_cc_meeting_details_for_download() -> None:
    latest_date = get_latest_downloaded_date()
    meetings_to_process = parse_meetings_from_url(latest_date)
    meeting_dates = sorted(
        [key for key in meetings_to_process.keys() if key > latest_date]
    )
    if meeting_dates:
        r.set(SCRAPED_CC_MTG_KEY, json.dumps(meeting_dates))
        r.expire(SCRAPED_CC_MTG_KEY, DEFAULT_ONE_WEEK_SECONDS_EXPIRATION)

@app.task
def download_unprocessed_videos() -> None:
    meetings_to_download = r.get(SCRAPED_CC_MTG_KEY)
    if meetings_to_download:
        meetings_to_download = json.loads(meetings_to_download)
    for date in meetings_to_download:
        details = r.hget(DETAIL_CC_MTG_KEY, date)
        if details:
            details = json.loads(details.decode('utf-8'))
        clip_id = details.get('ClipId')
        outfile = str(os.path.join(OUTFILE_LOCATION, OUTFILE_NAME.format(date)))
        print(f"outfile: {outfile}")
        if os.path.exists(outfile):
            logger.info(
                "Found outfile, skipping download...",
                extra={date: date, outfile: outfile},
            )
            continue
        print("No media found, downloading...")
        media_url = get_m3u_url(clip_id)
        if media_url:
            get_media_stream(media_url, outfile)
            r.hset(DOWNLOADED_CC_MTG_KEY, date, 1)

@app.task
def compress_downloaded_cc_meeting_videos() -> None:
    input_dir = DOWNLOADED_DIR
    output_dir = COMPRESSED_DIR
    options = COMPRESSION_OPTIONS
    pattern = COMPRESSION_PATTERN
    k = Compressor(output_dir, options, pattern, input_dir=input_dir)
    k.orchestrate()


def rip_audio_from_downloaded_cc_meeting_videos() -> None:
    input_dir = DOWNLOADED_DIR
    output_dir = AUDIO_DIR
    k = Ripper(output_dir, input_dir=input_dir)
    k.orchestrate()


app.conf.beat_schedule = {
    'get-cc-meeting-details-to-process': {
        'task': 'src.tasks.get_cc_meeting_details_for_download',
        'schedule': crontab(hour=23, minute=30),
    },
    'download-cc-meeting-video': {
        'task': 'src.tasks.download_unprocessed_videos',
        'schedule': crontab(hour=23, minute=45),
    },
    'compress-cc-meeting-video': {
        'task': 'src.tasks.compress_downloaded_cc_meeting_videos',
        'schedule': crontab(hour=1, minute=30),
    },
}