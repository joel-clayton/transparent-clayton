import json
import logging
import os
import re
import subprocess
from typing import List

import requests

from celery_app import r
from src.constants import SCRAPED_CC_MTG_KEY, DETAIL_CC_MTG_KEY
from src.processors.process import Processor

PLAYER_URL = (
    "https://claytonca.granicus.com/player/clip/{clip_id}?view_id=1&redirect=true"
)
logger = logging.getLogger(__name__)


class Downloader(Processor):

    def get_dates_to_process(self) -> List[str] | None:
        dates_to_upload = r.get(SCRAPED_CC_MTG_KEY)
        if dates_to_upload:
            return json.loads(dates_to_upload)
        return

    def process(self):
        meetings_to_download = self.get_dates_to_process()
        if not meetings_to_download:
            return

        for date in meetings_to_download:
            details = r.hget(DETAIL_CC_MTG_KEY, date)
            if details:
                details = json.loads(details.decode('utf-8'))
            clip_id = details.get('ClipId')
            outfile = self.construct_filepath_for_date(date)

            if os.path.exists(outfile):
                logger.info(
                    "Found outfile, skipping download...",
                    extra={date: date, outfile: outfile},
                )
                continue

            media_url = self.get_m3u_url(clip_id)
            if not media_url:
                raise f"Unable to find media url for date {date}"
            if media_url:
                self.get_media_stream(media_url, outfile)
                r.hset(self.redis_key, date, 1)

    def get_m3u_url(self, clip_id: str) -> str:
        response = requests.get(PLAYER_URL.format(clip_id=clip_id))
        response.raise_for_status()
        pattern = r"(https://archive-stream.*?playlist\.m3u8)"
        matches = re.findall(pattern, response.text)
        if matches:
            base = os.path.dirname(matches[0])
            url = base + "/chunklist.m3u8"
            return url
        return ''

    def get_media_stream(self, stream_url: str, output_file: str) -> None:
        ffmpeg_command = ["ffmpeg", "-i", stream_url, "-codec", "copy", output_file]
        subprocess.run(ffmpeg_command)

