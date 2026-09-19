"""Resolve each meeting type's Google Drive folder under the shared parent.

The transcripts and archived documents for every meeting type live under one
"Source Material" parent folder, in a per-type subfolder named
``{display_name} Meetings`` (e.g. "Planning Commission Meetings"). Types created
before their folder existed (Budget and Audit, Trails and Landscaping) get their
folder made on demand. Resolved ids are cached per (parent, name).
"""

import os
from typing import Any

from src.meeting_types import MeetingType

# Shared parent ("Source Material") holding one folder per meeting type.
SOURCE_MATERIAL_PARENT_ID = os.environ.get(
    "SOURCE_MATERIAL_PARENT_ID", "177gAQr7VqjlKNoqH-TbBkkQfoXG9yLGn"
)
_FOLDER_MIME = "application/vnd.google-apps.folder"
_cache: dict[tuple[str, str], str] = {}


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
        f"name = '{name}' and '{parent_id}' in parents "
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
