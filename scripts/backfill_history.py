"""Backfill pre-cutover meeting history from the CivicClerk OData API (TRA-169).

The city holds its full meeting history (back to 2002) in CivicClerk, reachable
through the same OData API the live scrape uses. This ingests that pre-cutover
history through the normal pipeline — in bounded, resumable batches — so nothing
already processed is redone (the stages' existence checks skip it).

Two stages:
  --stage docs  : archive each meeting's documents to Drive + add a wiki entry.
                  Cheap; no video, no transcription. Detail-driven, no floor change.
  --stage video : run the full A/V pipeline (download -> compress -> YouTube ->
                  extract -> transcribe -> transcript Doc -> wiki) for video
                  meetings, with the EARLIEST floor lowered for this run only.

Ingestion fetches OData one year at a time (a single decades-wide query times the
API out) and persists each meeting's detail exactly as the live scrape does, via
parse_meetings_from_civic_clerk_iframe (which uses OData directly — the retired
Granicus path is never involved).

RUN WITH worker/beat STOPPED: --execute writes shared Redis detail that a running
worker would otherwise start acting on. Audit is read-only.

    python scripts/backfill_history.py --stage docs                       # audit all
    python scripts/backfill_history.py --stage docs --execute
    python scripts/backfill_history.py --stage video --type pc_mtg --year 2019 --execute
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from celery_app import r
from src.constants import SCRAPED_KEY
from src.processors.compress import Compressor
from src.processors.download import Downloader
from src.processors.download_docs import DocumentDownloader
from src.processors.extract import Extractor
from src.processors.transcribe import Transcriber
from src.processors.update_wiki import WikiUpdater
from src.processors.upload_docs import DocumentUploader
from src.processors.upload_transcript import TranscriptUploader
from src.processors.upload_video import VideoUploader
from src.scrapers.cc_meetings import parse_meetings_from_civic_clerk_iframe
from src.scrapers.civic_clerk_api import (
    event_datetime,
    fetch_events,
    matches_category,
    merge_by_datetime,
    split_documents,
    video_url,
)
from src.scrapers.constants import CIVIC_CLERK_START_DATE
from src.scrapers.models import PipelineClass
from src.types import MEETING_TYPE_BY_SOURCE

# Reach the whole archive: the pipeline's normal EARLIEST floor is bypassed for the
# backfill (passed to Processor.process); a run never scrapes past the cutover,
# where the live pipeline takes over.
BACKFILL_FLOOR = datetime(2000, 1, 1)
CUTOVER = CIVIC_CLERK_START_DATE


def _year_windows(from_year: int, to_year: int) -> list[tuple[datetime, datetime]]:
    """[start, end) windows, one per year, clamped to the cutover."""
    windows = []
    for year in range(from_year, to_year + 1):
        start = max(BACKFILL_FLOOR, datetime(year, 1, 1))
        end = min(datetime(year + 1, 1, 1), CUTOVER)
        if start < end:
            windows.append((start, end))
    return windows


def _in_window(event: Any, start: datetime, end: datetime) -> bool:
    """Whether an event's datetime falls in [start, end); an unparseable one is
    skipped rather than crashing the audit (merge_by_datetime guards it too)."""
    try:
        return start < event_datetime(event) < end
    except (ValueError, TypeError):
        return False


def _type_meetings_in(
    events: list[Any], meeting_type: Any, start: datetime, end: datetime
) -> list[Any]:
    """This type's merged meetings within [start, end) from an already-fetched
    window of events (so a window is fetched once, not once per type)."""
    return merge_by_datetime(
        [
            e
            for e in events
            if matches_category(e, meeting_type) and _in_window(e, start, end)
        ]
    )


def _ingest_window(meeting_type: Any, start: datetime, end: datetime) -> list[Any]:
    """Persist detail for a type's meetings in a window (writes Redis), returning
    the Meeting records, via the same OData path the live scrape uses."""
    return parse_meetings_from_civic_clerk_iframe(start, meeting_type, end=end)


def _run_docs(source_type: Any, meeting_type: Any) -> None:
    DocumentDownloader(meeting_type).process()
    DocumentUploader(meeting_type).process()
    # The wiki stage floors via the base Processor, so pass the backfill floor or
    # historical year-page entries would be skipped.
    WikiUpdater(source_type).process(BACKFILL_FLOOR)


def _run_video(source_type: Any, meeting_type: Any, video_keys: list[str]) -> None:
    # Only Downloader is driven by the scraped worklist; the later A/V stages read
    # from the on-disk stage dirs, so already-downloaded historical videos (e.g.
    # City Council) flow through them even though they aren't in this list.
    r.set(meeting_type.redis_key(SCRAPED_KEY), json.dumps(sorted(set(video_keys))))
    # Downloader is driven by the scraped worklist (set above), not a date floor,
    # so it needs none; the later A/V stages do floor, so take the backfill floor.
    Downloader(source_type).process()
    Compressor(source_type).process(BACKFILL_FLOOR)
    VideoUploader(source_type).process(BACKFILL_FLOOR)
    Extractor(source_type).process(BACKFILL_FLOOR)
    Transcriber(source_type).process(BACKFILL_FLOOR)
    TranscriptUploader(source_type).process(BACKFILL_FLOOR)
    WikiUpdater(source_type).process(BACKFILL_FLOOR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["docs", "video"], default="docs")
    parser.add_argument("--type", dest="type_key", default=None, help="one type key")
    parser.add_argument("--year", type=int, default=None, help="a single year")
    parser.add_argument("--from-year", type=int, default=2002)
    parser.add_argument("--to-year", type=int, default=CUTOVER.year)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="ingest + run stages (default: read-only audit). Run with worker stopped.",
    )
    args = parser.parse_args()
    from_year = args.year or args.from_year
    to_year = args.year or args.to_year

    selected = [
        (st, mt)
        for st, mt in MEETING_TYPE_BY_SOURCE.items()
        if args.type_key in (None, mt.key)
    ]
    if not selected:
        parser.error(f"no meeting type matches --type {args.type_key!r}")

    mode = "EXECUTE (writes; run with worker stopped)" if args.execute else "AUDIT"
    print(
        f"History backfill — stage: {args.stage}  years: {from_year}-{to_year}  "
        f"mode: {mode}"
    )
    windows = _year_windows(from_year, to_year)
    totals: dict[str, int] = defaultdict(int)
    tally: dict[str, dict[str, int]] = {
        mt.display_name: {"video": 0, "docs": 0} for _st, mt in selected
    }

    if args.execute:
        # Ingest (persist detail) per type via the live-scrape path, then run that
        # type's stages. Workers must be stopped (this writes shared Redis detail).
        for source_type, meeting_type in selected:
            video_keys: list[str] = []
            for start, end in windows:
                for m in _ingest_window(meeting_type, start, end):
                    if m.get("pipeline_class") == PipelineClass.FULL.value:
                        video_keys.append(m["key"])
                        tally[meeting_type.display_name]["video"] += 1
                    else:
                        tally[meeting_type.display_name]["docs"] += 1
            if args.stage == "docs":
                _run_docs(source_type, meeting_type)
            else:
                _run_video(source_type, meeting_type, video_keys)
    else:
        # Audit: fetch each window once (read-only) and classify all selected types.
        for start, end in windows:
            events = fetch_events(start, end)
            for _source_type, meeting_type in selected:
                for m in _type_meetings_in(events, meeting_type, start, end):
                    # Classify exactly as the scrape does (video_url / split_documents),
                    # so the audit predicts --execute: a no-asset meeting is neither
                    # video nor docs and is skipped (execute never ingests it).
                    agenda, supplemental = split_documents(m)
                    if video_url(m):
                        tally[meeting_type.display_name]["video"] += 1
                    elif agenda or supplemental:
                        tally[meeting_type.display_name]["docs"] += 1

    for _st, meeting_type in selected:
        t = tally[meeting_type.display_name]
        print(
            f"  {meeting_type.display_name:<34} video={t['video']:>4} docs={t['docs']:>5}"
        )
        totals["video"] += t["video"]
        totals["docs"] += t["docs"]

    print(f"\nTOTAL  video={totals['video']}  docs-only={totals['docs']}")
    if not args.execute:
        print(
            "Audit only — no Redis writes, no stages run. Re-run with --execute "
            "(worker/beat stopped) to ingest + process."
        )


if __name__ == "__main__":
    main()
