"""Restore Drive-only transcripts to disk — a one-off Clayton data repair.

Historical transcripts were produced via TurboScribe.ai and uploaded to Drive, but
never written to disk; the pipeline only began persisting transcripts to disk after
the switch to AssemblyAI. The disk-based transcribe idempotency therefore can't see
them, so a history backfill would re-transcribe (re-spend) all of them.

This copies every Drive transcript Doc whose on-disk ``.txt`` is missing down to
disk, filling the hole. Afterwards the normal, generic (disk-based) pipeline needs
no Drive- or TurboScribe-specific logic — which keeps the workflow clean for reuse
in another city. This script is a one-off migration, NOT part of the workflow.

Read-only audit by default; ``--execute`` writes the ``.txt`` files to disk.

    python scripts/restore_transcripts_from_drive.py            # audit
    python scripts/restore_transcripts_from_drive.py --execute  # write to disk
"""

from __future__ import annotations

import argparse
import os
import re
from typing import Any

from src.meeting_types import MEETING_TYPES, MeetingType
from src.processors.helpers.drive_folders import find_type_folder
from src.settings import TRANSCRIBED_DIR

_FOLDER_MIME = "application/vnd.google-apps.folder"
_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
_TRANSCRIPT_SUFFIX = " - Transcript"
# Datetime in a Drive folder name uses a colon; the on-disk key uses an underscore.
_DT = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}[:_]\d{2} [AP]M")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def meeting_key_from_folder(name: str) -> str | None:
    """The underscore-form meeting key embedded in a Drive meeting-folder name."""
    dt = _DT.search(name)
    if dt:
        return dt.group(0).replace(":", "_")
    d = _DATE.search(name)
    return d.group(0) if d else None


def transcript_disk_path(root: str, meeting_type: MeetingType, key: str) -> str:
    """Where the transcribe stage expects this meeting's transcript on disk —
    ``<TRANSCRIBED>/<bucket>/<file_stub> <key> - City of Clayton.txt`` — so a file
    placed here is recognized by the generic disk-based idempotency."""
    name = meeting_type.file_template.format(key, ".txt")
    return os.path.join(root, meeting_type.disk_bucket, name)


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


def _transcript_text(service: Any, file_id: str, mime: str) -> str:
    """Plain-text content of a transcript Doc — exported for a Google Doc, or
    downloaded directly for a stored text file."""
    if mime == _GOOGLE_DOC_MIME:
        data = service.files().export(fileId=file_id, mimeType="text/plain").execute()
    else:
        data = (
            service.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
        )
    return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)


def restore(service: Any, root: str, execute: bool) -> tuple[int, int, int]:
    """Copy each Drive transcript Doc whose on-disk .txt is missing down to disk.
    Returns (restored, already-on-disk, skipped-unparseable)."""
    restored = present = skipped = 0
    for meeting_type in MEETING_TYPES:
        type_folder = find_type_folder(service, meeting_type)
        if not type_folder:
            continue
        for year in _drive_children(service, type_folder):
            if year.get("mimeType") != _FOLDER_MIME:
                continue
            for folder in _drive_children(service, year["id"]):
                if folder.get("mimeType") != _FOLDER_MIME:
                    continue
                transcript = next(
                    (
                        c
                        for c in _drive_children(service, folder["id"])
                        if (c.get("name") or "").endswith(_TRANSCRIPT_SUFFIX)
                    ),
                    None,
                )
                if transcript is None:
                    continue
                key = meeting_key_from_folder(folder["name"])
                if key is None:
                    skipped += 1
                    continue
                dest = transcript_disk_path(root, meeting_type, key)
                if os.path.exists(dest):
                    present += 1
                    continue
                print(
                    f"  restore: {meeting_type.disk_bucket}/{key} "
                    f"<- Drive {transcript['name']!r}"
                )
                if execute:
                    text = _transcript_text(
                        service, transcript["id"], transcript.get("mimeType", "")
                    )
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8") as handle:
                        handle.write(text)
                restored += 1
    return restored, present, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=TRANSCRIBED_DIR)
    parser.add_argument(
        "--execute", action="store_true", help="write .txt files (default: audit)"
    )
    args = parser.parse_args()

    from scripts.reconcile import _drive_service

    mode = "EXECUTE (writes to disk)" if args.execute else "AUDIT (read-only)"
    print(f"Restore Drive transcripts to disk — mode: {mode}")
    restored, present, skipped = restore(_drive_service(), args.root, args.execute)
    verb = "restored" if args.execute else "to restore"
    print(
        f"\n{restored} transcript(s) {verb}, {present} already on disk, "
        f"{skipped} skipped (unparseable folder name)"
    )


if __name__ == "__main__":
    main()
