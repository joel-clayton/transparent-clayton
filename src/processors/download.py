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
from src.scrapers.alerting import AlertLevel, alert
from src.settings import DOWNLOADED_DIR
from src.storage_layout import write_dir_for_bucket
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

        failures: List[str] = []
        for date in meetings_to_download:
            try:
                outfile = self.construct_filepath_for_date(date)
                if os.path.exists(outfile):
                    self.logger.info(
                        "Found outfile, skipping download...",
                        extra={date: date, outfile: outfile},
                    )
                    continue
                self._download_one(date, outfile)
                # Mark done only after a successful download; a raise leaves the
                # date unmarked so the next run retries it.
                r.hset(self.redis_key, date, 1)
                self.log_complete_for_date(date=date)
            except Exception as exc:
                # One meeting's failure (e.g. a Granicus 404 or unresolved media
                # URL) must not abort the rest of the batch — previously this
                # re-raised, blocking every meeting behind it. Log, collect, and
                # continue; a batched alert is sent below.
                self.logger.error("Failed %s for %s: %s", self.job_type.name, date, exc)
                failures.append(f"{date}: {exc}")

        if failures:
            alert(
                AlertLevel.ACTIONABLE,
                f"{len(failures)} {self.job_type.name} meeting(s) failed this run:"
                "\n- " + "\n- ".join(failures),
            )
        return None

    def _download_one(self, date: str, outfile: str) -> None:
        details_str: bytes | None = r.hget(
            self.meeting_type.redis_key(DETAIL_KEY), date
        )
        if not details_str:
            raise Exception(f"Could not parse meeting details from Redis for {date}")
        details = json.loads(details_str.decode("utf-8"))

        # CivicClerk stores a direct media URL in `video` (progressive MP4 on
        # cpmedia.azureedge.net). Granicus stores a player-page URL there and
        # requires resolving the HLS playlist via clip_id.
        video = details.get("video") or ""
        if "youtube" in video:
            self.logger.info("Trying youtube-dl method")
            from yt_dlp import YoutubeDL

            download_dir = write_dir_for_bucket(
                DOWNLOADED_DIR, self.meeting_type.disk_bucket
            )
            # Unlike ffmpeg (which errors on a missing dir), yt-dlp creates its
            # output tree itself, so guard the unmounted-volume case explicitly —
            # otherwise the download would land on the boot disk.
            if not os.path.isdir(download_dir):
                raise RuntimeError(
                    f"Storage volume not mounted; refusing to download to {download_dir}"
                )
            opts = {
                **YTDL_OPTS,
                "outtmpl": os.path.join(
                    download_dir, self.meeting_type.file_template_yt_dlp
                ).format(date),
            }
            # Let yt-dlp errors propagate so a failed download is treated as a
            # failure (not swallowed and then marked complete).
            with YoutubeDL(opts) as ydl:
                ydl.download([video])
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
