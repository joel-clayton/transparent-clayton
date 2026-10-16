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

Each legacy asset is classified against the OData API as CLEAN (same type, one
datetime match), REBUCKET (belongs to another type), COLLISION (present under two
buckets — manual merge), AMBIGUOUS (the date can't be resolved automatically), or
NOMATCH (no OData event that date). The executors apply CLEAN/REBUCKET across four
surfaces — disk files, Redis keys, Drive folders/files (URL-preserving), and
YouTube titles — and skip the rest, reporting them for manual review.

    python scripts/canonicalize_keys.py                      # audit all (read-only)
    python scripts/canonicalize_keys.py --surface disk       # audit one surface
    python scripts/canonicalize_keys.py --execute            # apply all
    python scripts/canonicalize_keys.py --surface drive --execute

Operator runs --execute (disk/Drive/YouTube/Redis writes are outward). Must run
before the TRA-169 OData backfill, with worker/beat stopped.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from celery_app import r
from src.constants import DATETIME_FORMAT
from src.meeting_types import MEETING_TYPES, MeetingType
from src.processors.helpers.drive_folders import (
    find_or_create_child_folder,
    find_or_create_type_folder,
    find_type_folder,
    meeting_folder_name,
)
from src.scrapers.civic_clerk_api import (
    effective_category,
    event_datetime,
    fetch_events,
    matches_category,
    merge_by_datetime,
)
from src.settings import STORAGE_ROOT

# Flat per-type stage dirs whose files embed the meeting key in the filename.
# Documents is deliberately NOT here: its key lives in the per-meeting *folder* name
# (the files inside are label-named), and legacy meetings have none on disk anyway;
# the durable document assets live in Drive and are handled by apply_drive. A
# date-only Documents folder, should one ever appear, is surfaced by
# warn_dated_documents rather than silently mishandled by the flat-file logic.
STAGES = ["Downloaded", "Compressed", "Audio", "Transcripts"]

# A datetime key carries a time; a date-only key does not. The separator is an
# underscore on disk/Redis ("07_00 PM") and a colon on Drive ("07:00 PM").
_TIME = r"\d{2}[:_]\d{2} [AP]M"
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


def fetch_events_resilient(
    start: datetime, end: datetime, attempts: int = 4
) -> list[dict[str, Any]]:
    """Fetch OData events, retrying on a transient failure (the CivicClerk API
    occasionally times out a page mid-pagination, which otherwise kills the whole
    one-shot migration). The fetch is read-only and idempotent, so a retry simply
    re-fetches from the first page."""
    for attempt in range(attempts):
        try:
            return fetch_events(start, end)
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            wait = 5 * (attempt + 1)
            print(
                f"  OData fetch failed ({exc}); retrying in {wait}s "
                f"({attempt + 1}/{attempts - 1})"
            )
            time.sleep(wait)
    return []  # unreachable; the last attempt re-raises


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
        plans.append(replace(plan, files=sorted(disk[(bucket, date)])))
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
    correct type bucket on a rebucket. Each meeting migrates all-or-nothing: if any
    of its stages' datetime targets already exist (a legacy copy beside a newer
    datetime one), the whole meeting is left intact and surfaced as a CONFLICT for
    manual merge, never half-renamed into a split date-only/datetime state. Files
    whose source is already gone (migrated in a prior run) are simply skipped.
    Returns (renamed, conflicts)."""
    renamed = conflicts = 0
    for p in plans:
        if p.classification not in (CLEAN, REBUCKET) or not p.new_key:
            continue
        assert p.new_bucket is not None
        moves: list[tuple[str, str, str, str]] = []  # (src, dst, src_dir, dst_dir)
        conflict = False
        for stage, name in p.files:
            src_dir = os.path.join(root, stage, p.src_bucket)
            dst_dir = os.path.join(root, stage, p.new_bucket)
            new_name = rekey_filename(
                name, p.date, p.new_key, p.src_bucket, p.new_bucket
            )
            src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, new_name)
            if not os.path.exists(src):
                continue  # already migrated in a prior run
            if os.path.exists(dst):
                conflict = True
                break
            moves.append((src, dst, src_dir, dst_dir))
        if conflict:
            print(
                f"  disk CONFLICT: {p.src_bucket}/{p.date} left intact — a datetime "
                f"copy already exists ({p.new_bucket}/{p.new_key}); manual merge"
            )
            conflicts += 1
            continue
        for src, dst, src_dir, dst_dir in moves:
            print(
                f"  disk: {os.path.relpath(src, root)} -> {os.path.relpath(dst, root)}"
            )
            if execute:
                os.makedirs(dst_dir, exist_ok=True)
                os.rename(src, dst)
                sidecar = os.path.join(src_dir, "._" + os.path.basename(src))
                if os.path.exists(sidecar):
                    os.rename(
                        sidecar, os.path.join(dst_dir, "._" + os.path.basename(dst))
                    )
            renamed += 1
    return renamed, conflicts


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
    index: dict[str, list[dict[str, Any]]],
    collisions: set[tuple[str | None, str | None]],
    execute: bool,
) -> tuple[int, int, int]:
    """Rename legacy date-only per-meeting Redis keys to their datetime key.
    ``RENAME`` is atomic and type-agnostic; guarded so an already-migrated key is
    never clobbered. Meetings the disk plan flagged COLLISION are left untouched.
    Returns (renamed, already-present, flagged)."""
    renamed = present = flagged = 0
    for old, new, plan in plan_redis(index):
        if new is not None and (plan.new_bucket, plan.new_key) in collisions:
            new = None  # cross-bucket duplicate; leave the whole meeting for manual
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


_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


def drive_child_name(child_name: str, old_folder: str, new_folder: str) -> str:
    """A transcript/doc file carries its meeting folder's name as its prefix, so
    retitling the folder means swapping that prefix (keeping the ' - <asset>' tag)."""
    return child_name.replace(old_folder, new_folder, 1)


def _drive_children(service: Any, parent_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{parent_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageToken=token,
            )
            .execute()
        )
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def _drive_rename(
    service: Any,
    file_id: str,
    new_name: str,
    add_parent: str | None = None,
    remove_parent: str | None = None,
) -> None:
    """Rename (and optionally reparent) a Drive file in one update — preserves the
    id, so the URL never changes."""
    kwargs: dict[str, Any] = {
        "fileId": file_id,
        "body": {"name": new_name},
        "fields": "id",
        "supportsAllDrives": True,
    }
    if add_parent:
        kwargs["addParents"] = add_parent
    if remove_parent:
        kwargs["removeParents"] = remove_parent
    service.files().update(**kwargs).execute()


def apply_drive(
    service: Any,
    index: dict[str, list[dict[str, Any]]],
    collisions: set[tuple[str | None, str | None]],
    execute: bool,
) -> tuple[int, int, int]:
    """Retitle each legacy date-only Drive meeting folder (and its transcript/doc
    files) to the colon datetime name, moving the folder to the correct type's tree
    on a rebucket. All renames preserve the Drive file id, so every URL is unchanged
    (TRA-166). Meetings the disk plan flagged COLLISION are left untouched. Returns
    (renamed, already-datetime, flagged)."""
    renamed = present = flagged = 0
    for mt in MEETING_TYPES:
        type_folder = find_type_folder(service, mt)
        if not type_folder:
            continue
        for year in _drive_children(service, type_folder):
            if year.get("mimeType") != _DRIVE_FOLDER_MIME:
                continue
            year_name = year["name"]
            year_children = _drive_children(service, year["id"])
            # Folder-name set per destination year, so the duplicate-target guard is
            # a lookup, not a fresh listing per meeting (the CLEAN dest is this same
            # year, already fetched here). Updated as we go so same-run renames count.
            dest_names: dict[str, set[str]] = {
                year["id"]: {
                    c["name"]
                    for c in year_children
                    if c.get("mimeType") == _DRIVE_FOLDER_MIME
                }
            }
            for folder in year_children:
                if folder.get("mimeType") != _DRIVE_FOLDER_MIME:
                    continue
                name = folder["name"]
                if _DATETIME_RE.search(name):
                    present += 1
                    continue
                m = _DATE_RE.search(name)
                if not m:
                    continue
                plan = classify(mt.display_name, m.group(1), index.get(m.group(1), []))
                if plan.classification not in (CLEAN, REBUCKET) or not plan.new_key:
                    flagged += 1
                    continue
                if (plan.new_bucket, plan.new_key) in collisions:
                    flagged += 1  # cross-bucket duplicate; leave for manual merge
                    continue
                assert plan.new_bucket is not None
                new_mt = BUCKET_TO_TYPE[plan.new_bucket]
                new_name = meeting_folder_name(new_mt, plan.new_key)
                move = plan.classification == REBUCKET
                print(
                    f"  drive: {mt.display_name}/{year_name}/{name} -> "
                    f"{new_mt.display_name}/{year_name}/{new_name}"
                )
                if not execute:
                    renamed += 1
                    continue
                if move:
                    dest_type = find_or_create_type_folder(service, new_mt)
                    dest_year_id = find_or_create_child_folder(
                        service, dest_type, year_name
                    )
                else:
                    dest_year_id = year["id"]
                if dest_year_id not in dest_names:
                    dest_names[dest_year_id] = {
                        c["name"]
                        for c in _drive_children(service, dest_year_id)
                        if c.get("mimeType") == _DRIVE_FOLDER_MIME
                    }
                # Drive allows same-named siblings, so a datetime folder the newer
                # pipeline already created would be duplicated by this rename and the
                # meeting's assets split. Guard both CLEAN (same year) and REBUCKET
                # (destination year): if the target folder already exists, skip + flag.
                if new_name in dest_names[dest_year_id]:
                    print("    (skip: destination already has this meeting folder)")
                    flagged += 1
                    continue
                for child in _drive_children(service, folder["id"]):
                    child_new = drive_child_name(child["name"], name, new_name)
                    if child_new != child["name"]:
                        _drive_rename(service, child["id"], child_new)
                if move:
                    _drive_rename(
                        service,
                        folder["id"],
                        new_name,
                        add_parent=dest_year_id,
                        remove_parent=year["id"],
                    )
                else:
                    _drive_rename(service, folder["id"], new_name)
                dest_names[dest_year_id].add(new_name)
                renamed += 1
    return renamed, present, flagged


def youtube_title_date(title: str) -> str | None:
    """The meeting date from a YouTube title, normalized to YYYY-MM-DD. Legacy
    titles use either hyphens ("2021-09-21") or an older space form ("2025 05 19")."""
    m = re.search(r"(\d{4})[- ](\d{2})[- ](\d{2})", title)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def youtube_canonical_title(src_title: str, new_mt: MeetingType, new_key: str) -> str:
    """The exact title ``upload_video`` would set for this meeting's canonical
    (datetime) compressed file — built from the type's own ``video_title_datetime_
    format`` (so a rebucket gets the new type's name), with any ' part N' preserved.
    Matching it is what stops the backfill re-uploading the video."""
    part = re.search(r" part \d+", src_title)
    base = datetime.strptime(new_key, DATETIME_FORMAT).strftime(
        new_mt.video_title_datetime_format
    )
    return base + (part.group(0) if part else "")


def _youtube_meeting_type(title: str) -> MeetingType | None:
    """The meeting type for a YouTube title. Uploaded titles embed the type's
    ``display_name`` ("Clayton CA GHAD Meeting ..."), which differs from its
    ``title_match`` for GHAD — so match on display_name, not mt_classify."""
    for mt in MEETING_TYPES:
        if f"{mt.display_name} Meeting" in title:
            return mt
    return None


def _youtube_set_title(service: Any, video_id: str, new_title: str) -> bool:
    """Retitle a video, preserving its other snippet fields (categoryId is required
    on a snippet update). Returns False if the video is no longer available."""
    resp = service.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return False
    snippet = items[0]["snippet"]
    snippet["title"] = new_title
    service.videos().update(
        part="snippet", body={"id": video_id, "snippet": snippet}
    ).execute()
    return True


def apply_youtube(
    service: Any,
    index: dict[str, list[dict[str, Any]]],
    collisions: set[tuple[str | None, str | None]],
    execute: bool,
) -> tuple[int, int, int]:
    """Retitle each legacy date-only / space-dated YouTube video to its canonical
    datetime title (and correct type name on a rebucket). Meetings the disk plan
    flagged COLLISION are left untouched. Returns (renamed, already-datetime,
    flagged)."""
    from scripts.reconcile import _fetch_uploads

    renamed = present = flagged = 0
    # First pass: resolve each legacy title to its canonical form, and record the
    # titles already in canonical datetime form (left untouched).
    retitles: list[tuple[str, str, str]] = []  # (video_id, old_title, new_title)
    existing: set[str] = set()
    for title, video_id in _fetch_uploads(service):
        if _DATETIME_RE.search(title):
            present += 1
            existing.add(title)
            continue
        src_mt = _youtube_meeting_type(title)
        date = youtube_title_date(title)
        if src_mt is None or date is None:
            continue  # not a meeting video (e.g. a one-off demo upload)
        plan = classify(src_mt.display_name, date, index.get(date, []))
        if plan.classification not in (CLEAN, REBUCKET) or not plan.new_key:
            flagged += 1
            continue
        if (plan.new_bucket, plan.new_key) in collisions:
            flagged += 1  # cross-bucket duplicate; leave for manual merge
            continue
        assert plan.new_bucket is not None
        new_title = youtube_canonical_title(
            title, BUCKET_TO_TYPE[plan.new_bucket], plan.new_key
        )
        retitles.append((video_id, title, new_title))

    # A legacy video must not be retitled onto a title another video already holds —
    # whether another video we are also retitling (an intra-batch duplicate) or an
    # already-canonical one we left untouched (``existing``). Either produces two
    # identically-titled videos and defeats the title-match dedupe the backfill
    # relies on, so flag instead of retitling.
    taken = existing | {
        t for t, n in Counter(new for _v, _o, new in retitles).items() if n > 1
    }
    for video_id, old_title, new_title in retitles:
        if new_title in taken:
            print(
                f"  youtube COLLISION: {old_title!r} -> {new_title!r} (target exists)"
            )
            flagged += 1
            continue
        print(f"  youtube: {old_title!r} -> {new_title!r}")
        if execute and not _youtube_set_title(service, video_id, new_title):
            print("    (skipped: video unavailable)")
            flagged += 1
            continue
        renamed += 1
    return renamed, present, flagged


def warn_dated_documents(root: str) -> int:
    """Surface any date-only Documents folders, which the flat-file logic can't
    migrate (their key is in the folder name, files inside are label-named). None
    exist today, but flag rather than silently skip if that ever changes."""
    base = os.path.join(root, "Documents")
    found: list[str] = []
    if os.path.isdir(base):
        for bucket in os.listdir(base):
            bucket_dir = os.path.join(base, bucket)
            if not os.path.isdir(bucket_dir):
                continue
            for entry in os.listdir(bucket_dir):
                if entry.startswith("._") or _DATETIME_RE.search(entry):
                    continue
                if _DATE_RE.search(entry) and os.path.isdir(
                    os.path.join(bucket_dir, entry)
                ):
                    found.append(f"{bucket}/{entry}")
    if found:
        print(
            f"\nWARNING: {len(found)} date-only Documents folder(s) NOT handled by "
            "this migration (dated-folder layout) — handle manually:"
        )
        for f in sorted(found):
            print(f"  Documents/{f}")
    return len(found)


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
    events = fetch_events_resilient(datetime(2000, 1, 1), datetime.now())
    index = index_events(events)

    # The disk inventory is the authoritative duplicate detector, so build it even
    # when only another surface is requested: a meeting flagged COLLISION there is
    # left untouched on every surface so the two copies can be merged by hand.
    plans = build_plan(args.root, index)
    collisions = {
        (p.new_bucket, p.new_key) for p in plans if p.classification == COLLISION
    }

    if args.surface in ("disk", "all"):
        _print_audit(plans)
        warn_dated_documents(args.root)
        renamed, conflicts = apply_disk(args.root, plans, args.execute)
        print(
            f"\ndisk: {renamed} file(s) {applied}, {conflicts} CONFLICT "
            "(legacy + datetime copy both present — manual merge)"
        )

    if args.surface in ("redis", "all"):
        renamed, present, flagged = apply_redis(index, collisions, args.execute)
        print(
            f"redis: {renamed} key(s) {applied}, {present} already datetime-keyed, "
            f"{flagged} flagged (ambiguous/no-match/collision, manual review)"
        )

    if args.surface in ("drive", "all"):
        from scripts.reconcile import _drive_service

        renamed, present, flagged = apply_drive(
            _drive_service(), index, collisions, args.execute
        )
        print(
            f"drive: {renamed} folder(s) {applied}, {present} already datetime-keyed, "
            f"{flagged} flagged"
        )

    if args.surface in ("youtube", "all"):
        from scripts.reconcile import _youtube_service

        renamed, present, flagged = apply_youtube(
            _youtube_service(), index, collisions, args.execute
        )
        print(
            f"youtube: {renamed} title(s) {applied}, {present} already datetime-titled, "
            f"{flagged} flagged"
        )


if __name__ == "__main__":
    main()
