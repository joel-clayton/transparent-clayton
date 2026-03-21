import os

from celery_app import app
from src.av.download import (OUTFILE_LOCATION, OUTFILE_NAME, get_m3u_url,
                             get_media_stream, logger)
from src.av.kompress import Kompressor
from src.scrapers.cc_meetings.cc_meetings import \
    get_cc_meeting_details_for_download
from src.scrapers.cc_meetings.constants import (COMPRESSED_DIR,
                                                COMPRESSION_OPTIONS,
                                                COMPRESSION_PATTERN,
                                                DOWNLOADED_DIR)


@app.task
def download_unprocessed_videos() -> None:
    meetings_to_download = get_cc_meeting_details_for_download()
    for date, clip_id in meetings_to_download.items():
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
        get_media_stream(media_url, outfile)


@app.task
def compress_downloaded_cc_meeting_videos() -> None:
    input_dir = DOWNLOADED_DIR
    output_dir = COMPRESSED_DIR
    options = COMPRESSION_OPTIONS
    pattern = COMPRESSION_PATTERN
    k = Kompressor(output_dir, options, pattern, input_dir=input_dir)
    k.orchestrate()
