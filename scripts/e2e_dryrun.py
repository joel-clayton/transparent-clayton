"""End-to-end dry run against the LIVE city website.

What it does, per configured meeting type, for meetings from ``--start`` to now:
  1. Scrapes the real CivicClerk portal (this is the only part that hits the
     network / drives a browser).
  2. Simulates each asset stage by writing a dummy file where that stage's
     output would go, so the following stage has its input. Nothing is really
     downloaded, compressed, extracted, or transcribed.
  3. Mocks the upload stages (YouTube / Drive): no uploads happen; dummy links
     are stored so the wiki has something to point at.
  4. Prints the intended wiki page name + content to stdout (year pages and the
     combined transparency page). The wiki is NOT modified.

Isolation (so a dry run never touches production):
  * PIPELINE_STORAGE_ROOT -> a throwaway dir under tests/, wiped each run.
  * REDIS_DB -> a separate Redis database, flushed each run.
It refuses to run against REDIS_DB=0 or to wipe a "/Volumes" path.

Usage:
    python scripts/e2e_dryrun.py --start "2026-08-01"
    python scripts/e2e_dryrun.py --start "2026-08-01 00:00"
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from typing import Mapping

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Isolate BEFORE importing the pipeline — modules capture these at import time.
DEFAULT_ASSET_ROOT = os.path.join(_REPO_ROOT, "tests", "e2e_dryrun_assets")
os.environ.setdefault("PIPELINE_STORAGE_ROOT", DEFAULT_ASSET_ROOT)
os.environ.setdefault("REDIS_DB", "15")

from celery_app import r  # noqa: E402
from src.constants import NO_ASSETS_KEY  # noqa: E402
from src.meeting_types import MeetingType  # noqa: E402
from src.processors import update_wiki  # noqa: E402
from src.processors.archive_docs import docs_to_archive  # noqa: E402
from src.processors.compress import Compressor  # noqa: E402
from src.processors.download import Downloader  # noqa: E402
from src.processors.extract import Extractor  # noqa: E402
from src.processors.transcribe import Transcriber  # noqa: E402
from src.processors.update_wiki import (  # noqa: E402
    WIKI_NO_MATERIALS_PAGE,
    WikiUpdater,
    render_no_materials_page,
)
from src.scrapers.cc_meetings import parse_meetings_from_url  # noqa: E402
from src.scrapers.models import PipelineClass  # noqa: E402
from src.types import MEETING_TYPE_BY_SOURCE  # noqa: E402

DUMMY_VIDEO_URL = "https://youtu.be/DRYRUNvideo"
DUMMY_DRIVE_URL = "https://drive.google.com/file/d/DRYRUN/view"


def _silence_discord() -> None:
    def _noop(*_args: object, **_kwargs: object) -> None:
        pass

    update_wiki.send_to_discord_bots = _noop  # type: ignore[assignment]


class DryRunWikiUpdater(WikiUpdater):
    """WikiUpdater that never logs in or saves — it prints intended output."""

    def authenticate(self) -> object:  # type: ignore[override]
        return None

    def get_sections_from_wiki_page(self, page_name: str) -> list:
        return []  # treat every year page as empty, so all entries render

    def update_page_sections_for_page(
        self, page_name: str, sections: list, date: str
    ) -> None:
        print(f"\n===== WIKI PAGE (would save): {page_name} =====")
        for section in sections:
            print(f"{section.title}{section.content}")

    def update_transparency_page(self) -> None:
        entries: list[tuple[str, str]] = []
        for meeting_type in MEETING_TYPE_BY_SOURCE.values():
            for member in r.smembers(meeting_type.redis_key(NO_ASSETS_KEY)) or set():
                key = member.decode("utf-8") if isinstance(member, bytes) else member
                entries.append((meeting_type.display_name, key))
        print(f"\n===== WIKI PAGE (would save): {WIKI_NO_MATERIALS_PAGE} =====")
        print(render_no_materials_page(entries))


def _write_dummy(path: str, content: bytes = b"dry-run placeholder\n") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


def _simulate_av_assets(
    date: str,
    downloader: Downloader,
    compressor: Compressor,
    extractor: Extractor,
    transcriber: Transcriber,
) -> None:
    _write_dummy(downloader.construct_filepath_for_date(date))
    # The compressed template carries an ffmpeg %03d segment token; use one part.
    _write_dummy(compressor.construct_filepath_for_date(date).replace("%03d", "000"))
    _write_dummy(extractor.construct_filepath_for_date(date))
    _write_dummy(
        transcriber.construct_filepath_for_date(date),
        b"[Speaker A] (0:00:00 - 0:00:05)\nDry-run transcript.\n",
    )
    print(f"  [dummy assets] downloaded/compressed/extracted/transcribed for {date}")


def _mock_uploads(meeting_type: MeetingType, date: str) -> None:
    video_key = meeting_type.video_link_key_template.format(
        meeting_key=date, part_num=1
    )
    r.set(video_key, DUMMY_VIDEO_URL)
    transcript_key = meeting_type.transcript_link_key_template.format(meeting_key=date)
    r.set(transcript_key, DUMMY_DRIVE_URL)
    print(f"  [MOCK upload] youtube {video_key} + drive transcript {transcript_key}")


def _mock_doc_archive(
    meeting_type: MeetingType, date: str, meeting: Mapping[str, object]
) -> None:
    docs = docs_to_archive(meeting)
    if not docs:
        return
    doc_key = meeting_type.doc_link_key_template.format(meeting_key=date)
    for label in docs:
        r.hset(doc_key, label, DUMMY_DRIVE_URL)
    print(f"  [MOCK drive archive] {date}: {list(docs)}")


def _reset_assets(asset_root: str) -> None:
    if os.path.isdir(asset_root):
        shutil.rmtree(asset_root)
    for sub in ("Downloaded", "Compressed", "Audio", "Transcripts", "RawSnapshots"):
        os.makedirs(os.path.join(asset_root, sub), exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end dry run against the live city website."
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Lookback datetime, e.g. '2026-08-01' or '2026-08-01 00:00'.",
    )
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start)

    if int(os.environ["REDIS_DB"]) == 0:
        sys.exit("Refusing to run against REDIS_DB=0; use an isolated DB (e.g. 15).")
    asset_root = os.environ["PIPELINE_STORAGE_ROOT"]
    if "/Volumes" in asset_root:
        sys.exit(f"Refusing to wipe a volume path: {asset_root}")

    print(f"Dry run from {start} to now")
    print(f"  assets  -> {asset_root} (wiped)")
    print(f"  redis   -> db {os.environ['REDIS_DB']} (flushed)")
    _reset_assets(asset_root)
    r.flushdb()  # isolated DB only (guarded above)
    _silence_discord()

    for source_type, meeting_type in MEETING_TYPE_BY_SOURCE.items():
        print(f"\n########## {meeting_type.display_name} ##########")
        meetings = parse_meetings_from_url(start, meeting_type)
        print(f"scraped {len(meetings)} meeting(s) with assets since {start}")

        downloader = Downloader(source_type)
        compressor = Compressor(source_type)
        extractor = Extractor(source_type)
        transcriber = Transcriber(source_type)

        for meeting in meetings:
            date = meeting["key"]
            if meeting.get("pipeline_class") == PipelineClass.FULL.value:
                _simulate_av_assets(
                    date, downloader, compressor, extractor, transcriber
                )
                _mock_uploads(meeting_type, date)
            _mock_doc_archive(meeting_type, date, meeting)

        DryRunWikiUpdater(source_type).process()

    print("\nDry run complete. No uploads made; wiki not modified.")


if __name__ == "__main__":
    main()
