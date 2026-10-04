"""Migrate on-disk assets from the flat layout into per-type buckets (TRA-135).

Historically each stage directory was flat, with the meeting type encoded only in
the filename::

    <ROOT>/Compressed/Clayton CA City Council Meeting 2026-05-26 ... .mp4

The pipeline now reads and writes per-type buckets::

    <ROOT>/Compressed/City Council/Clayton CA City Council Meeting ... .mp4

The pipeline still *reads* the flat layout during the transition, so running this
is not time-critical — but until it runs, new writes and old files live in two
places. This tool moves the existing flat files/folders into their type's bucket.

Read-only by default; it only moves anything with ``--execute``::

    python scripts/migrate_disk_layout.py            # audit (read-only)
    python scripts/migrate_disk_layout.py --execute  # perform the migration

A file whose type can't be determined, or whose destination already holds a file
of the same name, is left in place and reported — never overwritten.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.meeting_types import MEETING_TYPES, MeetingType  # noqa: E402
from src.settings import (  # noqa: E402
    COMPRESSED_DIR,
    DOCUMENTS_DIR,
    DOWNLOADED_DIR,
    EXTRACTED_AUDIO_DIR,
    TRANSCRIBED_DIR,
)
from src.util import get_date_or_datetime_string_from_string  # noqa: E402

# Stage dirs holding per-meeting files (one file per date/part).
STAGE_DIRS = [DOWNLOADED_DIR, COMPRESSED_DIR, EXTRACTED_AUDIO_DIR, TRANSCRIBED_DIR]
# Set of bucket names, used to skip already-migrated bucket dirs when scanning.
_BUCKETS = {mt.disk_bucket for mt in MEETING_TYPES}


def type_for_name(name: str) -> MeetingType | None:
    """The meeting type whose file_stub appears in a file/folder name, or None if
    zero or more than one match (stubs are mutually non-overlapping, so a real
    asset matches exactly one)."""
    matches = [mt for mt in MEETING_TYPES if mt.file_stub in name]
    return matches[0] if len(matches) == 1 else None


def _plan_move(src_dir: str, name: str, bucket: str) -> tuple[str, str]:
    """(source path, destination path) for moving ``name`` into its bucket."""
    return os.path.join(src_dir, name), os.path.join(src_dir, bucket, name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the migration (default is a read-only audit)",
    )
    args = parser.parse_args()

    moves = 0
    skips: list[str] = []

    def _do_move(src: str, dest: str, label: str) -> None:
        nonlocal moves
        if os.path.exists(dest):
            skips.append(f"destination already exists, left in place: {dest}")
            return
        print(f"  move {label}: {src} -> {dest}")
        if args.execute:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
        moves += 1

    # Stage files: flat files directly under each stage dir -> <stage>/<bucket>/.
    for stage in STAGE_DIRS:
        if not os.path.isdir(stage):
            continue
        for name in sorted(os.listdir(stage)):
            full = os.path.join(stage, name)
            if not os.path.isfile(full):
                continue  # a bucket subdir (already migrated) or other dir
            if name.startswith("._"):
                continue  # macOS AppleDouble sidecar — leave it
            mt = type_for_name(name)
            if mt is None:
                skips.append(f"unrecognized type, left in place: {full}")
                continue
            src, dest = _plan_move(stage, name, mt.disk_bucket)
            _do_move(src, dest, name)

    # Document folders: flat <stub> <key>/ dirs under DOCUMENTS_DIR -> bucketed.
    if os.path.isdir(DOCUMENTS_DIR):
        for name in sorted(os.listdir(DOCUMENTS_DIR)):
            full = os.path.join(DOCUMENTS_DIR, name)
            if not os.path.isdir(full) or name.startswith("._"):
                continue
            if name in _BUCKETS:
                continue  # already a per-type bucket
            mt = type_for_name(name)
            key = get_date_or_datetime_string_from_string(name)
            if mt is None or not key:
                skips.append(f"unrecognized document folder, left in place: {full}")
                continue
            src, dest = _plan_move(DOCUMENTS_DIR, name, mt.disk_bucket)
            _do_move(src, dest, name)

    verb = "Applied" if args.execute else "Would apply"
    print(f"\n{verb}: move {moves} file(s)/folder(s) into per-type buckets.")
    if skips:
        print(f"\n{len(skips)} item(s) left in place:")
        for s in skips:
            print(f"  {s}")
    if not args.execute:
        print("\n(read-only audit — re-run with --execute to apply.)")


if __name__ == "__main__":
    main()
