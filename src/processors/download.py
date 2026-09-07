import json
import os
import re
import shutil
import subprocess
from typing import Any, List

import requests

from celery_app import r
from src.constants import DETAIL_KEY, SCRAPED_KEY, DOWNLOADED_KEY
from src.processors.process import Processor
from src.settings import DOWNLOADED_DIR
from src.types import JobType, SourceType, MEETING_TYPE_BY_SOURCE

PLAYER_URL = (
    "https://claytonca.granicus.com/player/clip/{clip_id}?view_id=1&redirect=true"
)
# outtmpl is set per-download from the meeting type's yt-dlp filename template.
YTDL_OPTS: dict[str, Any] = {
    "recodevideo": "mp4",
    "format": "bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]",
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
}

# yt-dlp needs a JS runtime for some YouTube clients. Prefer an explicit
# DENO_PATH, else discover deno on PATH; omit the option entirely if absent so
# this runs on any host rather than a hardcoded developer path.
DENO_PATH = os.environ.get("DENO_PATH") or shutil.which("deno")
if DENO_PATH:
    YTDL_OPTS["js_runtimes"] = {"deno": {"path": DENO_PATH}}


class Downloader(Processor):
    def __init__(
        self, source_type: SourceType = SourceType.CITY_COUNCIL_MEETING
    ) -> None:
        self.job_type = JobType.DOWNLOAD
        self.source_type = source_type
        self.meeting_type = MEETING_TYPE_BY_SOURCE[source_type]
        self.redis_key = self.meeting_type.redis_key(DOWNLOADED_KEY)
        super().__init__()

    def gather_input_dates(self) -> List:
        dates_to_upload = r.get(self.meeting_type.redis_key(SCRAPED_KEY))
        if dates_to_upload:
            return json.loads(dates_to_upload)
        return []

    def gather_output_dates(self) -> List:
        return self.gather_dates(DOWNLOADED_DIR)

    def process(self) -> None:
        meetings_to_download = self.get_most_recent_missing_dates()
        if not meetings_to_download:
            return None

        for date in meetings_to_download:
            details_str: bytes | None = r.hget(
                self.meeting_type.redis_key(DETAIL_KEY), date
            )
            details = {}
            if not details_str:
                raise Exception(
                    f"Could not parse meeting details from Redis for {date}"
                )
            details = json.loads(details_str.decode("utf-8"))
            outfile = self.construct_filepath_for_date(date)
            if os.path.exists(outfile):
                self.logger.info(
                    "Found outfile, skipping download...",
                    extra={date: date, outfile: outfile},
                )
                continue

            # CivicClerk stores a direct media URL in `video` (progressive MP4
            # on cpmedia.azureedge.net). Granicus stores a player-page URL
            # there and requires resolving the HLS playlist via clip_id.
            video = details.get("video") or ""
            if "youtube" in video:
                self.logger.error("Trying youtube-dl method")
                from yt_dlp import YoutubeDL

                opts = {
                    **YTDL_OPTS,
                    "outtmpl": os.path.join(
                        DOWNLOADED_DIR, self.meeting_type.file_template_yt_dlp
                    ).format(date),
                }
                try:
                    with YoutubeDL(opts) as ydl:
                        ydl.download([video])
                except Exception as ex:
                    print(f"yt error: {ex}")

            elif video.endswith((".mp4", ".m3u8")):
                self.logger.debug("Trying civicclerk method")
                self.get_media_stream(video, outfile)
            else:
                self.logger.debug("Trying granicus method")
                clip_id = details.get("clip_id")
                if not clip_id:
                    raise Exception("No Clip ID found in City Council Meeting details")
                media_url = self.get_m3u_url(clip_id)
                if not media_url:
                    raise Exception(f"Unable to find media url for date {date}")
                self.get_media_stream(media_url, outfile)

            outfile = outfile.replace(":", "\\:")
            r.hset(self.redis_key, date, 1)
            self.log_complete_for_date(date=date)

        return None

    def get_m3u_url(self, clip_id: str) -> str:
        response = requests.get(PLAYER_URL.format(clip_id=clip_id))
        response.raise_for_status()
        pattern = r"(https://archive-stream.*?playlist\.m3u8)"
        matches = re.findall(pattern, response.text)
        if matches:
            base = os.path.dirname(matches[0])
            url = base + "/chunklist.m3u8"
            return url
        return ""

    def get_media_stream(self, stream_url: str, output_file: str) -> None:
        ffmpeg_command = [
            "ffmpeg",
            "-i",
            stream_url,
            "-codec",
            "copy",
            f"{output_file}",
        ]
        subprocess.run(ffmpeg_command)
