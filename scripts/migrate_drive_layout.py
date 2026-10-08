"""Migrate the Google Drive layout to one folder per meeting (TRA-166).

Old layout, under ``<Source Material>/<Type> Meetings/``:
  * transcripts sit directly in a ``<year>`` folder, and
  * each meeting's document folder (``<file_stub> <meeting_key>``) sits at the type
    level, beside the year folders.

New layout: ``<Type> Meetings/<year>/<meeting folder>/`` (colon datetime) holding
both the transcript and that meeting's documents, each file type-tagged by name:
``... - Transcript`` and ``... - <city label>``.

This MOVES and RENAMES (reparent / rename via files.update) — it never copies — so
every file keeps its id and therefore its URL: the wiki transcript/doc links stay
valid and no asset URL changes. It reparents legacy document folders under their
year (renamed to the colon display), retags their documents, and moves transcripts
into the meeting folder with the "- Transcript" tag. Read-only audit by default;
``--execute`` applies. Idempotent: already-tagged/placed items are skipped.

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

from src.constants import DATE_PATTERN  # noqa: E402
from src.meeting_types import MEETING_TYPES, MeetingType  # noqa: E402
from src.processors.helpers.drive_folders import (  # noqa: E402
    ASSET_SEP,
    document_file_name,
    find_or_create_child_folder,
    find_or_create_type_folder,
    find_type_folder,
    meeting_folder_name,
    transcript_file_name,
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
# Datetime in a Drive name, with either the colon form ("07:00 PM", new transcript
# Docs) or the legacy underscore form ("07_00 PM", old on-disk-derived folder names).
_DT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}[:_][0-9]{2} [APM]{2}")


def meeting_key_from_drive_name(name: str) -> str | None:
    """The internal meeting key (underscore time form) embedded in a Drive name, or
    None — matching either the colon or legacy underscore rendering. Date-only
    fallback."""
    dt = _DT_RE.search(name)
    if dt:
        return dt.group(0).replace(":", "_")  # internal key uses the underscore form
    d = re.search(DATE_PATTERN, name)
    return d.group(0) if d else None


def _rename(service: Any, file_id: str, new_name: str) -> None:
    service.files().update(
        fileId=file_id,
        body={"name": new_name},
        fields="id",
        supportsAllDrives=True,
    ).execute()


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


def _retag_documents(
    service: Any, folder_id: str, mt: MeetingType, key: str, execute: bool
) -> int:
    """Rename each document in a meeting folder to "<meeting display> - <label>",
    skipping files already tagged (idempotent). Returns the count."""
    renamed = 0
    prefix = meeting_folder_name(mt, key) + ASSET_SEP
    for item in _list_children(service, folder_id):
        if item.get("mimeType") == _FOLDER_MIME:
            continue
        name = item["name"]
        if name.startswith(prefix):
            continue  # already type-tagged (a prior run, or the transcript)
        new_name = document_file_name(mt, key, name)  # name is the bare city label
        print(f"    rename doc {name!r} -> {new_name!r}")
        if execute:
            _rename(service, item["id"], new_name)
        renamed += 1
    return renamed


def _migrate_type(service: Any, mt: MeetingType, execute: bool) -> tuple[int, int, int]:
    """Returns (folders_moved, transcripts_moved, docs_renamed) for one type.

    Reparents AND renames (both preserve the file id/URL): legacy document folders
    move under their year and are renamed to the colon display; their documents are
    retagged "<display> - <label>"; and transcripts move into the meeting folder and
    gain the "- Transcript" tag."""
    folders_moved = 0
    transcripts_moved = 0
    docs_renamed = 0
    # Audit must not write: only create the type folder under --execute; if it
    # doesn't exist yet there is nothing to migrate for this type.
    if execute:
        type_id = find_or_create_type_folder(service, mt)
    else:
        found = find_type_folder(service, mt)
        if found is None:
            return 0, 0, 0
        type_id = found
    type_children = _list_children(service, type_id)

    # Pass A: a legacy document folder sits at the type level named
    # "<file_stub> <key>" (underscore). Move it under its year, rename it to the
    # colon display, and retag its documents. (Year folders — 4 digits — stay put.)
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
        new_folder = meeting_folder_name(mt, key)  # colon display
        print(f"  move doc folder {name!r} -> {year}/{new_folder}/")
        if execute:
            year_id = find_or_create_child_folder(service, type_id, year)
            _reparent(service, child["id"], add=year_id, remove=type_id)
            if name != new_folder:
                _rename(service, child["id"], new_folder)
        folders_moved += 1
        docs_renamed += _retag_documents(service, child["id"], mt, key, execute)

    # Pass B: a transcript Doc sits directly in a year folder. Move it into the
    # meeting's folder (the one pass A moved, or a new one), and tag it "- Transcript".
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
            new_name = transcript_file_name(mt, key)
            # Only a legacy transcript (named exactly the meeting display, or already
            # tagged) — never a stray "<display> - <label>" document misfiled directly
            # in the year folder.
            if item["name"] != folder and item["name"] != new_name:
                continue
            print(
                f"  move transcript {item['name']!r} -> "
                f"{child['name']}/{folder}/ (as {new_name!r})"
            )
            if execute:
                meeting_id = find_or_create_child_folder(service, year_id, folder)
                _reparent(service, item["id"], add=meeting_id, remove=year_id)
                if item["name"] != new_name:
                    _rename(service, item["id"], new_name)
            transcripts_moved += 1

    return folders_moved, transcripts_moved, docs_renamed


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
    docs = 0
    for mt in MEETING_TYPES:
        print(f"{mt.drive_folder_name}:")
        f, t, d = _migrate_type(service, mt, args.execute)
        folders += f
        transcripts += t
        docs += d

    verb = "Applied" if args.execute else "Would apply"
    print(
        f"\n{verb}: move {folders} document folder(s) under their year, "
        f"{transcripts} transcript(s) into their meeting folder, and retag "
        f"{docs} document(s). File ids/URLs are preserved (reparented/renamed, "
        "not copied)."
    )
    if not args.execute:
        print("\n(read-only audit — re-run with --execute to apply.)")


if __name__ == "__main__":
    main()
