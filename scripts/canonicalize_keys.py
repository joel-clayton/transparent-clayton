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
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from celery_app import r
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
TYPE_BY_KEY = {mt.key: mt for mt in MEETING_TYPES}

CLEAN = "CLEAN"
REBUCKET = "REBUCKET"
AMBIGUOUS = "AMBIGUOUS"
NOMATCH = "NOMATCH"
# The same meeting is present under two buckets (a mis-filed copy alongside the
# correctly-filed one), so both resolve to the same target — needs manual merge.
COLLISION = "COLLISION"


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


def index_events(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group OData events by their date (``YYYY-MM-DD``) for same-day lookup."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        try:
            index[event_datetime(e).strftime("%Y-%m-%d")].append(e)
        except (ValueError, TypeError):
            continue
    return index


def build_plan(root: str, index: dict[str, list[dict[str, Any]]]) -> list[Plan]:
    """Classify every legacy date-only asset on disk against the OData events."""
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
    return mark_collisions(plans)


def mark_collisions(plans: list[Plan]) -> list[Plan]:
    """Flag meetings present under two buckets. When more than one CLEAN/REBUCKET
    plan resolves to the same (bucket, datetime key) target — e.g. a meeting
    mis-filed into City Council that also exists in its correct committee bucket —
    canonicalizing both would create duplicate/partial assets, so mark them all
    COLLISION for manual merge rather than guessing."""
    by_target: dict[tuple[str | None, str | None], list[Plan]] = defaultdict(list)
    for p in plans:
        if p.classification in (CLEAN, REBUCKET):
            by_target[(p.new_bucket, p.new_key)].append(p)
    collided = {
        (p.src_bucket, p.date)
        for group in by_target.values()
        if len(group) > 1
        for p in group
    }
    return [
        replace(
            p,
            classification=COLLISION,
            note=f"also present as {p.new_bucket} {p.new_key} in another bucket",
        )
        if (p.src_bucket, p.date) in collided
        else p
        for p in plans
    ]


# --- pure name/key transforms (unit-tested) ---


def rekey_filename(
    old_name: str, date: str, new_key: str, src_display: str, new_display: str
) -> str:
    """Rewrite an on-disk filename to its datetime key (and new type stub on a
    rebucket). Pure string surgery: swap the display-name stub when the type
    changed, then expand the date to the full datetime key (``new_key`` begins
    with ``date``, so this inserts the time)."""
    name = old_name
    if src_display != new_display:
        name = name.replace(src_display, new_display, 1)
    return name.replace(date, new_key, 1)


def remap_redis_key(
    old_key: str, date: str, new_key: str, src_type_key: str, new_type_key: str
) -> str:
    """Rewrite a per-meeting Redis key (``<family>.<type>.<date>[.<part>]``) to its
    datetime key (and new type on a rebucket), preserving any trailing part."""
    return old_key.replace(f".{src_type_key}.", f".{new_type_key}.", 1).replace(
        date, new_key, 1
    )


# --- executors (apply the plan to each surface; read-only unless execute) ---


def apply_disk(root: str, plans: list[Plan], execute: bool) -> tuple[int, int]:
    """Rename each CLEAN/REBUCKET asset's files to the datetime key, moving to the
    correct type bucket on a rebucket. Idempotent: skips when the target already
    exists or the source is gone. Returns (renamed, already-present)."""
    renamed = present = 0
    for p in plans:
        if p.classification not in (CLEAN, REBUCKET) or not p.new_key:
            continue
        assert p.new_bucket is not None
        for stage, name in p.files:
            src_dir = os.path.join(root, stage, p.src_bucket)
            dst_dir = os.path.join(root, stage, p.new_bucket)
            new_name = rekey_filename(
                name, p.date, p.new_key, p.src_bucket, p.new_bucket
            )
            src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, new_name)
            if not os.path.exists(src):
                continue
            if os.path.exists(dst):
                present += 1
                continue
            print(f"  disk {stage}: {p.src_bucket}/{name} -> {p.new_bucket}/{new_name}")
            if execute:
                os.makedirs(dst_dir, exist_ok=True)
                os.rename(src, dst)
                sidecar = os.path.join(src_dir, "._" + name)
                if os.path.exists(sidecar):
                    os.rename(sidecar, os.path.join(dst_dir, "._" + new_name))
            renamed += 1
    return renamed, present


def plan_redis(
    index: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, str | None, Plan]]:
    """Find legacy date-only per-meeting Redis keys and resolve each to its new
    (type, datetime) key via the same classifier. Returns (old_key, new_key|None,
    plan); new_key is None for AMBIGUOUS/NOMATCH."""
    out: list[tuple[str, str | None, Plan]] = []
    for raw in r.scan_iter(match="*_link.*"):
        key = raw.decode() if isinstance(raw, bytes) else raw
        m = _DATE_RE.search(key)
        if not m or _DATETIME_RE.search(key):
            continue
        date = m.group(1)
        type_key = key.split(".")[1]
        mt = TYPE_BY_KEY.get(type_key)
        if mt is None:
            continue
        plan = classify(mt.display_name, date, index.get(date, []))
        if plan.classification in (CLEAN, REBUCKET) and plan.new_key:
            assert plan.new_type_key is not None
            new = remap_redis_key(key, date, plan.new_key, type_key, plan.new_type_key)
            out.append((key, new, plan))
        else:
            out.append((key, None, plan))

    # Guard against two legacy keys resolving to the same target (a meeting present
    # under two types): drop both so a rename can never clobber the other's value.
    target_counts = Counter(new for _old, new, _p in out if new is not None)
    return [
        (old, (new if new is None or target_counts[new] == 1 else None), plan)
        for old, new, plan in out
    ]


def apply_redis(
    index: dict[str, list[dict[str, Any]]], execute: bool
) -> tuple[int, int, int]:
    """Rename legacy date-only per-meeting Redis keys to their datetime key.
    ``RENAME`` is atomic and type-agnostic; guarded so an already-migrated key is
    never clobbered. Returns (renamed, already-present, flagged)."""
    renamed = present = flagged = 0
    for old, new, plan in plan_redis(index):
        if new is None:
            flagged += 1
            continue
        if r.exists(new):
            present += 1
            continue
        print(f"  redis: {old} -> {new}")
        if execute:
            try:
                r.rename(old, new)
            except Exception as exc:  # old vanished mid-run; next run retries
                print(f"    (skipped: {exc})")
                continue
        renamed += 1
    return renamed, present, flagged


def _print_audit(plans: list[Plan]) -> None:
    by_class: dict[str, list[Plan]] = defaultdict(list)
    for p in plans:
        by_class[p.classification].append(p)

    print(f"Legacy date-only assets: {len(plans)}")
    for cls in (CLEAN, REBUCKET, COLLISION, AMBIGUOUS, NOMATCH):
        print(f"  {cls:<10} {len(by_class[cls])}")

    if by_class[REBUCKET]:
        print("\n-- REBUCKET (move to correct type + add time) --")
        for p in by_class[REBUCKET]:
            print(
                f"  [{p.src_bucket}] {p.date} -> [{p.new_bucket}] {p.new_key}  "
                f"({len(p.files)} file(s))"
            )
    for cls, label in (
        (COLLISION, "COLLISION (same meeting in two buckets — manual merge)"),
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
    parser.add_argument(
        "--surface",
        choices=["disk", "redis", "drive", "youtube", "all"],
        default="all",
        help="which surface to canonicalize (default: all)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="apply the renames (default: audit / read-only dry run)",
    )
    args = parser.parse_args()
    applied = "applied" if args.execute else "planned"

    mode = "EXECUTE (writes)" if args.execute else "AUDIT (read-only)"
    print(f"Canonicalization — mode: {mode}  surface: {args.surface}")
    events = fetch_events(datetime(2000, 1, 1), datetime.now())
    index = index_events(events)

    if args.surface in ("disk", "all"):
        plans = build_plan(args.root, index)
        _print_audit(plans)
        renamed, present = apply_disk(args.root, plans, args.execute)
        print(f"\ndisk: {renamed} file(s) {applied}, {present} already datetime-keyed")

    if args.surface in ("redis", "all"):
        renamed, present, flagged = apply_redis(index, args.execute)
        print(
            f"redis: {renamed} key(s) {applied}, {present} already datetime-keyed, "
            f"{flagged} flagged (ambiguous/no-match, left for manual review)"
        )


if __name__ == "__main__":
    main()
