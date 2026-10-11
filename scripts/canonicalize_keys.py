"""Canonicalize legacy date-only assets to datetime keys (TRA-168).

Datetime-key support was added after a body of assets already existed, and those
were never renamed. So early meetings are stored under a *date-only* key
(``City Council Meeting 2025-08-11``) while newer ones use a *datetime* key
(``... 2026-01-06 06_55 PM``). The planned OData historical backfill (TRA-169)
always computes a datetime key, so it would not match these legacy assets and
would re-download / re-upload / duplicate them. This migration rewrites the
legacy assets to their datetime key first, using the CivicClerk OData API as the
source of truth, and moves assets that were mis-filed into the wrong type bucket
(the old City Council catch-all) to their correct type.

The planner here is the audit: it classifies every legacy date-only asset as
CLEAN (same type, one datetime match), REBUCKET (belongs to another type),
AMBIGUOUS (the date can't be resolved automatically), or NOMATCH (no OData event
on that date). Executors (disk/Redis/YouTube/Drive) consume this plan; they are
added on top of this core. Read-only by default.

    python scripts/canonicalize_keys.py            # audit (read-only)
"""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.constants import DATETIME_FORMAT
from src.meeting_types import MEETING_TYPES, MeetingType
from src.scrapers.civic_clerk_api import (
    effective_category,
    event_datetime,
    fetch_events,
    matches_category,
    merge_by_datetime,
)
from src.settings import STORAGE_ROOT

# Stage directories keyed by meeting-type bucket (per-type subfolders, TRA-135).
STAGES = ["Downloaded", "Compressed", "Audio", "Transcripts", "Documents"]

# A datetime key carries a time ("07_00 PM"); a date-only key does not.
_TIME = r"\d{2}_\d{2} [AP]M"
_DATE = r"\d{4}-\d{2}-\d{2}"
_DATETIME_RE = re.compile(rf"{_DATE} {_TIME}")
_DATE_RE = re.compile(rf"({_DATE})")

BUCKET_TO_TYPE = {mt.display_name: mt for mt in MEETING_TYPES}
CATEGORY_TO_TYPE = {mt.category: mt for mt in MEETING_TYPES}

CLEAN = "CLEAN"
REBUCKET = "REBUCKET"
AMBIGUOUS = "AMBIGUOUS"
NOMATCH = "NOMATCH"


@dataclass(frozen=True)
class Plan:
    """What should happen to one legacy date-only meeting (one src bucket + date)."""

    src_bucket: str
    date: str
    classification: str
    new_bucket: str | None = None  # correct type bucket (== src_bucket when CLEAN)
    new_key: str | None = None  # full datetime key, e.g. "2025-08-11 07_00 PM"
    new_type_key: str | None = None  # Redis type key, e.g. "cc_mtg"
    note: str = ""
    files: list[tuple[str, str]] = field(default_factory=list)  # (stage, filename)


def _key_for(event: dict[str, Any]) -> str:
    """The pipeline meeting key (datetime form) for an OData event."""
    return event_datetime(event).strftime(DATETIME_FORMAT)


def classify(src_bucket: str, date: str, events_on_date: list[dict[str, Any]]) -> Plan:
    """Classify one on-disk (bucket, date-only) asset against that date's OData
    events. Uses the scraper's own ``matches_category`` / ``merge_by_datetime`` so
    the result mirrors how ingestion would key the same meeting."""
    src_type = BUCKET_TO_TYPE[src_bucket]
    merged = merge_by_datetime(events_on_date)

    same = [e for e in merged if matches_category(e, src_type)]
    if len(same) == 1:
        return Plan(
            src_bucket, date, CLEAN, src_bucket, _key_for(same[0]), src_type.key
        )
    if len(same) > 1:
        times = sorted(_key_for(e) for e in same)
        return Plan(
            src_bucket, date, AMBIGUOUS, note=f"{len(same)} same-type meetings: {times}"
        )

    # No meeting of this type on this date: maybe it was mis-filed from another type.
    by_type: dict[MeetingType, list[dict[str, Any]]] = defaultdict(list)
    for e in merged:
        resolved = CATEGORY_TO_TYPE.get(effective_category(e))
        if resolved is not None:
            by_type[resolved].append(e)

    if not by_type:
        return Plan(src_bucket, date, NOMATCH, note="no OData event on this date")

    if len(by_type) == 1:
        ((mt, evs),) = by_type.items()
        if len(evs) == 1:
            return Plan(
                src_bucket,
                date,
                REBUCKET,
                mt.display_name,
                _key_for(evs[0]),
                mt.key,
                note=f"move {src_bucket} -> {mt.display_name}",
            )

    opts = {
        mt.display_name: sorted(_key_for(e) for e in evs) for mt, evs in by_type.items()
    }
    return Plan(src_bucket, date, AMBIGUOUS, note=f"candidates: {opts}")


def inventory_disk(root: str) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Map ``(bucket, date-only key)`` -> list of ``(stage, filename)`` for every
    legacy date-only asset on disk. Skips datetime-keyed files and macOS ``._``
    sidecars."""
    found: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for stage in STAGES:
        base = os.path.join(root, stage)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            bucket = os.path.relpath(dirpath, base).split(os.sep)[0]
            if bucket not in BUCKET_TO_TYPE:
                continue
            for name in files:
                if name.startswith("._") or _DATETIME_RE.search(name):
                    continue
                m = _DATE_RE.search(name)
                if m:
                    found[(bucket, m.group(1))].append((stage, name))
    return found


def build_plan(root: str, events: list[dict[str, Any]]) -> list[Plan]:
    """Classify every legacy date-only asset on disk against the OData events."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        try:
            index[event_datetime(e).strftime("%Y-%m-%d")].append(e)
        except (ValueError, TypeError):
            continue

    disk = inventory_disk(root)
    plans: list[Plan] = []
    for bucket, date in sorted(disk):
        plan = classify(bucket, date, index.get(date, []))
        plans.append(
            Plan(
                plan.src_bucket,
                plan.date,
                plan.classification,
                plan.new_bucket,
                plan.new_key,
                plan.new_type_key,
                plan.note,
                files=sorted(disk[(bucket, date)]),
            )
        )
    return plans


def _print_audit(plans: list[Plan]) -> None:
    by_class: dict[str, list[Plan]] = defaultdict(list)
    for p in plans:
        by_class[p.classification].append(p)

    print(f"Legacy date-only assets: {len(plans)}")
    for cls in (CLEAN, REBUCKET, AMBIGUOUS, NOMATCH):
        print(f"  {cls:<10} {len(by_class[cls])}")

    if by_class[REBUCKET]:
        print("\n-- REBUCKET (move to correct type + add time) --")
        for p in by_class[REBUCKET]:
            print(
                f"  [{p.src_bucket}] {p.date} -> [{p.new_bucket}] {p.new_key}  "
                f"({len(p.files)} file(s))"
            )
    for cls, label in (
        (AMBIGUOUS, "AMBIGUOUS (needs a human decision)"),
        (NOMATCH, "NOMATCH (no OData event that date)"),
    ):
        if by_class[cls]:
            print(f"\n-- {label} --")
            for p in by_class[cls]:
                print(
                    f"  [{p.src_bucket}] {p.date}  {p.note}  ({len(p.files)} file(s))"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=STORAGE_ROOT, help="storage root (default: %(default)s)"
    )
    args = parser.parse_args()

    print("Canonicalization audit — mode: AUDIT (read-only)")
    events = fetch_events(datetime(2000, 1, 1), datetime.now())
    plans = build_plan(args.root, events)
    _print_audit(plans)


if __name__ == "__main__":
    main()
