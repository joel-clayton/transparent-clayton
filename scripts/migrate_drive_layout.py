"""Migrate the Google Drive layout to one folder per meeting (TRA-166).

Old layout, under ``<Source Material>/<Type> Meetings/``:
  * transcripts sit directly in a ``<year>`` folder, and
  * each meeting's document folder (``<file_stub> <meeting_key>``) sits at the type
    level, beside the year folders.

New layout: ``<Type> Meetings/<year>/<file_stub> <meeting_key>/`` holding both the
transcript and that meeting's documents.

This MOVES files/folders (reparent via files.update add/removeParents) — it never
copies — so every file keeps its id and therefore its URL: the wiki transcript/doc
links stay valid and no asset URL changes. Read-only audit by default; ``--execute``
performs the reparenting. Idempotent: re-running after a partial move is safe.

    python scripts/migrate_drive_layout.py            # audit (read-only)
    python scripts/migrate_drive_layout.py --execute  # perform the moves

Operator runs this (Drive writes are outward). Sequence: stop worker/beat, deploy
the TRA-166 code, run this, restart, then confirm with scripts/reconcile.py.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.constants import DATE_PATTERN, DATETIME_OUTPUT_PATTERN  # noqa: E402
from src.meeting_types import MEETING_TYPES, MeetingType  # noqa: E402
from src.processors.helpers.drive_folders import (  # noqa: E402
    find_or_create_child_folder,
    find_or_create_type_folder,
    find_type_folder,
    meeting_folder_name,
)
from src.processors.helpers.google_auth import load_credentials  # noqa: E402
from src.processors.upload_transcript import (  # noqa: E402
    DESKTOP_APP_CLIENT_SECRET,
    DRIVE_TOKEN_FILE,
    SCOPES,
)
from src.util import get_year_string_from_string  # noqa: E402

import googleapiclient.discovery  # noqa: E402

_FOLDER_MIME = "application/vnd.google-apps.folder"
_YEAR_RE = re.compile(r"^\d{4}$")


def meeting_key_from_drive_name(name: str) -> str | None:
    """The internal meeting key embedded in a Drive file/folder name, or None.

    Transcript Doc names carry a datetime with a colon (``07:00 PM``); the internal
    key and the doc-folder name use an underscore (``07_00 PM``), so the colon form
    is normalized here. Falls back to a date-only key."""
    dt = re.search(DATETIME_OUTPUT_PATTERN, name)
    if dt:
        return dt.group(0).replace(":", "_")
    d = re.search(DATE_PATTERN, name)
    return d.group(0) if d else None


def _list_children(service: Any, parent_id: str) -> list[dict]:
    out: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{parent_id}' in parents and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        out.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _reparent(service: Any, file_id: str, add: str, remove: str) -> None:
    service.files().update(
        fileId=file_id,
        addParents=add,
        removeParents=remove,
        fields="id",
        supportsAllDrives=True,
    ).execute()


def _service() -> Any:
    creds = load_credentials(
        scopes=SCOPES,
        client_secret_path=DESKTOP_APP_CLIENT_SECRET,
        token_path=DRIVE_TOKEN_FILE,
    )
    return googleapiclient.discovery.build("drive", "v3", credentials=creds)


def _migrate_type(service: Any, mt: MeetingType, execute: bool) -> tuple[int, int]:
    """Returns (folders_moved, transcripts_moved) for one meeting type."""
    folders_moved = 0
    transcripts_moved = 0
    # Audit must not write: only create the type folder under --execute; if it
    # doesn't exist yet there is nothing to migrate for this type.
    if execute:
        type_id = find_or_create_type_folder(service, mt)
    else:
        found = find_type_folder(service, mt)
        if found is None:
            return 0, 0
        type_id = found
    type_children = _list_children(service, type_id)

    # Pass A: a legacy document folder sits at the type level named
    # "<file_stub> <key>". Move it under its year, where it becomes the meeting's
    # shared folder. (Year folders — name == 4 digits — stay put.)
    for child in type_children:
        if child.get("mimeType") != _FOLDER_MIME:
            continue
        name = child["name"]
        if _YEAR_RE.match(name):
            continue
        key = meeting_key_from_drive_name(name)
        if key is None or not name.startswith(mt.file_stub):
            continue
        year = get_year_string_from_string(key)
        if not year:
            continue
        print(f"  move doc folder {name!r} -> {year}/")
        if execute:
            year_id = find_or_create_child_folder(service, type_id, year)
            _reparent(service, child["id"], add=year_id, remove=type_id)
        folders_moved += 1

    # Pass B: a transcript Doc sits directly in a year folder. Move it into the
    # meeting's folder (the doc folder moved in pass A, or a new one), so the
    # transcript and documents end up together.
    for child in type_children:
        if child.get("mimeType") != _FOLDER_MIME or not _YEAR_RE.match(child["name"]):
            continue
        year_id = child["id"]
        for item in _list_children(service, year_id):
            if item.get("mimeType") == _FOLDER_MIME:
                continue  # a per-meeting folder, already in place
            key = meeting_key_from_drive_name(item["name"])
            if key is None or mt.file_stub not in item["name"]:
                continue
            folder = meeting_folder_name(mt, key)
            print(f"  move transcript {item['name']!r} -> {child['name']}/{folder}/")
            if execute:
                meeting_id = find_or_create_child_folder(service, year_id, folder)
                _reparent(service, item["id"], add=meeting_id, remove=year_id)
            transcripts_moved += 1

    return folders_moved, transcripts_moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the moves (default is a read-only audit)",
    )
    args = parser.parse_args()

    service = _service()
    folders = 0
    transcripts = 0
    for mt in MEETING_TYPES:
        print(f"{mt.drive_folder_name}:")
        f, t = _migrate_type(service, mt, args.execute)
        folders += f
        transcripts += t

    verb = "Applied" if args.execute else "Would apply"
    print(
        f"\n{verb}: move {folders} document folder(s) under their year and "
        f"{transcripts} transcript(s) into their meeting folder. "
        "File ids/URLs are preserved (reparented, not copied)."
    )
    if not args.execute:
        print("\n(read-only audit — re-run with --execute to apply.)")


if __name__ == "__main__":
    main()
