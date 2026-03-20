import os

from src.av.download import OUTFILE_LOCATION, OUTFILE_NAME, logger, get_m3u_url, \
    get_media_stream
from celery_app import app
from src.scrapers.cc_meetings.cc_meetings import get_cc_meeting_details_for_download
from src.scrapers.cc_meetings.constants import DOWNLOADED_DIR, COMPRESSED_DIR, \
    COMPRESSION_OPTIONS, COMPRESSION_PATTERN
from src.av.kompress import Kompressor


@app.task
def download_unprocessed_videos():
    meetings_to_download = get_cc_meeting_details_for_download()
    for date, clip_id in meetings_to_download.items():
        outfile = os.path.join(OUTFILE_LOCATION, OUTFILE_NAME.format(date))
        print(f"outfile: {outfile}")
        if os.path.exists(outfile):
            logger.info(f"Found outfile, skipping download...",
                        extra={
                            date: date,
                            outfile: outfile
                        })
            continue
        print("No media found, downloading...")
        media_url = get_m3u_url(clip_id)
        get_media_stream(media_url, outfile)

@app.task
def compress_downloaded_cc_meeting_videos():
    input_dir = DOWNLOADED_DIR
    output_dir = COMPRESSED_DIR
    options = COMPRESSION_OPTIONS
    pattern = COMPRESSION_PATTERN
    k = Kompressor(output_dir, options, pattern, input_dir=input_dir)
    k.orchestrate()
