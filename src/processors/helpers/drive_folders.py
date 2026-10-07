"""Resolve each meeting type's Google Drive folder under the shared parent.

The transcripts and archived documents for every meeting type live under one
"Source Material" parent folder, in a per-type subfolder named
``{display_name} Meetings`` (e.g. "Planning Commission Meetings"). Types created
before their folder existed (Budget and Audit, Trails and Landscaping) get their
folder made on demand. Resolved ids are cached per (parent, name).
"""

from typing import Any

from src.meeting_types import MeetingType

# Shared parent ("Source Material") holding one folder per meeting type. Sourced
# from the central settings surface (env-overridable via SOURCE_MATERIAL_PARENT_ID);
# re-exported here so existing importers keep the name.
from src.settings import SOURCE_MATERIAL_PARENT_ID

_FOLDER_MIME = "application/vnd.google-apps.folder"
_cache: dict[tuple[str, str], str] = {}


def escape_drive_query_value(value: str) -> str:
    """Escape a value for a Drive ``q`` string literal (backslash then single quote).
    Without it a name with an apostrophe (e.g. "Mayor's Report") produces an invalid
    query and a 400. Use at every site that interpolates a name into a Drive query."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create_type_folder(
    service: Any, meeting_type: MeetingType, parent_id: str = SOURCE_MATERIAL_PARENT_ID
) -> str:
    """Return the Drive folder id for ``meeting_type`` under ``parent_id``.

    Finds the existing ``{display_name} Meetings`` folder, creating it if absent.
    """
    name = meeting_type.drive_folder_name
    cache_key = (parent_id, name)
    if cache_key in _cache:
        return _cache[cache_key]

    query = (
        f"name = '{escape_drive_query_value(name)}' and '{parent_id}' in parents "
        f"and mimeType = '{_FOLDER_MIME}' and trashed = false"
    )
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id)", supportsAllDrives=True)
        .execute()
    )
    folders = results.get("files", [])
    if folders:
        folder_id = folders[0]["id"]
    else:
        created = (
            service.files()
            .create(
                body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        folder_id = created["id"]
    _cache[cache_key] = folder_id
    return folder_id
