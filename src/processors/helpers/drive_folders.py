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


def meeting_folder_name(meeting_type: MeetingType, meeting_key: str) -> str:
    """The per-meeting Drive folder name shared by this meeting's transcript and its
    documents (TRA-166), e.g. "City Council Meeting 2026-05-26 07_00 PM". The two
    uploaders must agree on this exactly so both land in the same folder."""
    return f"{meeting_type.file_stub} {meeting_key}"


def find_type_folder(
    service: Any, meeting_type: MeetingType, parent_id: str = SOURCE_MATERIAL_PARENT_ID
) -> str | None:
    """The type's Drive folder id if it already exists, else None — a find-only
    lookup (no creation), for read-only callers like the migration audit."""
    return _find_child_folder(service, parent_id, meeting_type.drive_folder_name)


def _find_child_folder(service: Any, parent_id: str, name: str) -> str | None:
    query = (
        f"name = '{escape_drive_query_value(name)}' and '{parent_id}' in parents "
        f"and mimeType = '{_FOLDER_MIME}' and trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    folders = results.get("files", [])
    return folders[0]["id"] if folders else None


def find_or_create_child_folder(service: Any, parent_id: str, name: str) -> str:
    """Folder id for ``name`` directly under ``parent_id``, creating it if absent."""
    existing = _find_child_folder(service, parent_id, name)
    if existing:
        return existing
    created = (
        service.files()
        .create(
            body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return created["id"]


def find_or_create_meeting_folder(
    service: Any, type_folder_id: str, year: str, folder_name: str
) -> str:
    """Folder id for ``<type_folder>/<year>/<folder_name>``, creating the year and
    meeting folders as needed (TRA-166). Both the transcript and document uploaders
    call this so a meeting's transcript and documents share one folder."""
    year_id = find_or_create_child_folder(service, type_folder_id, year)
    return find_or_create_child_folder(service, year_id, folder_name)


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
    # Reuse the shared find-or-create (one source of the query/create + all-drives
    # flags); only the per-type result is cached here.
    folder_id = find_or_create_child_folder(service, parent_id, name)
    _cache[cache_key] = folder_id
    return folder_id
