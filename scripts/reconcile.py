"""Three-way reconciliation: disk <-> YouTube <-> Redis (TRA-122).

Read-only audit that cross-checks the pipeline's three sources of truth for every
meeting, per type, and reports mismatches:

    python scripts/reconcile.py                 # READ-ONLY audit (default)
    python scripts/reconcile.py --type pc_mtg   # limit to one meeting-type key
    python scripts/reconcile.py --reconcile     # audit, then fix what it safely can

What it compares, per meeting key:

    disk      assets present under STORAGE_ROOT (Downloaded / Compressed /
              Audio / Transcripts), matched by the type's file stub
    youtube   videos uploaded to the channel (paginated across every page),
              matched to a type by the compressed-title prefix
    redis     detail hash, video_link.* / transcript_link.* pointers

Mismatches reported:

    STALE_VIDEO_LINK    video_link points at a video id no longer on the channel
                        (this is what breaks the wiki "Video Backup" links)
    MISSING_VIDEO_LINK  a video is uploaded but no video_link points at it
    MISSING_UPLOAD      a compressed part is on disk but not uploaded
    NO_DETAIL           on disk and/or on YouTube but absent from the detail hash
    TRANSCRIPT_NO_LINK  a transcript file is on disk but no transcript_link is set

The optional ``--reconcile`` step only drives existing workflows; it does not
reimplement pipeline logic:

  * repoints video_link/transcript pointers by calling the uploader's own
    ``get_recent_video_titles`` (which rewrites the links for every live video),
  * deletes video_link pointers with no surviving video (nothing to point at),
  * flags each affected meeting for re-render by adding it to the type's
    ``wiki_refresh`` set, which ``WikiUpdater`` already consumes on its next run.

Reconcile only writes to Redis; the actual wiki edit happens on the next
scheduled WikiUpdater run (or a manual one). The audit itself changes nothing.

Supersedes the throwaway ``scripts/dedup_youtube.py``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from googleapiclient.discovery import build  # noqa: E402

from celery_app import r  # noqa: E402
from datetime import datetime  # noqa: E402

from src.constants import (  # noqa: E402
    DATE_FORMAT,
    DATE_PATTERN,
    DATETIME_FORMAT,
    DATETIME_PATTERN,
    DETAIL_KEY,
    WIKI_REFRESH_KEY,
)
from src.meeting_types import MeetingType  # noqa: E402
from src.processors.constants import EARLIEST  # noqa: E402
from src.processors.helpers.google_auth import load_credentials  # noqa: E402
from src.processors.upload_transcript import (  # noqa: E402
    DESKTOP_APP_CLIENT_SECRET,
    DRIVE_TOKEN_FILE,
    SCOPES as DRIVE_SCOPES,
)
from src.processors.upload_video import (  # noqa: E402
    CLIENT_SECRETS_FILE,
    MAX_UPLOADS_PAGES,
    RESULT_COUNT,
    SCOPES,
    YOUTUBE_TOKEN_FILE,
    VideoUploader,
)
from src.publishers import is_destination_enabled  # noqa: E402
from src.settings import (  # noqa: E402
    COMPRESSED_DIR,
    DOCUMENTS_DIR,
    DOWNLOADED_DIR,
    EXTRACTED_AUDIO_DIR,
    TRANSCRIBED_DIR,
)
from src.types import MEETING_TYPE_BY_SOURCE, SourceType  # noqa: E402
from src.util import (  # noqa: E402
    get_date_or_datetime_string_from_string,
    get_part_num_from_string,
    get_year_string_from_string,
)

# Stage directories keyed by a short label for the report.
STAGE_DIRS: dict[str, str] = {
    "downloaded": DOWNLOADED_DIR,
    "compressed": COMPRESSED_DIR,
    "audio": EXTRACTED_AUDIO_DIR,
    "transcript": TRANSCRIBED_DIR,
}


def _meeting_key_from_filename(name: str) -> str | None:
    """The date/datetime meeting key embedded in a filename, or None if the name
    doesn't carry a recognized (pipeline-era) date."""
    match = re.search(DATETIME_PATTERN, name) or re.search(DATE_PATTERN, name)
    return match.group(0) if match else None


def _video_id_from_link(link: str) -> str:
    """Extract the YouTube video id from a stored watch URL."""
    return link.split("v=", 1)[1] if "v=" in link else link


def _key_datetime(key: str) -> datetime | None:
    """Parse a meeting key ('2026-05-26 07_00 PM' or '2026-05-26') to a datetime."""
    for fmt in (DATETIME_FORMAT, DATE_FORMAT):
        try:
            return datetime.strptime(key, fmt)
        except ValueError:
            continue
    return None


@dataclass
class MeetingRow:
    key: str
    on_disk: set[str] = field(default_factory=set)  # stage labels present
    disk_parts: set[int] = field(default_factory=set)  # compressed part numbers
    yt_parts: dict[int, str] = field(default_factory=dict)  # part -> live video id
    link_parts: dict[int, str] = field(default_factory=dict)  # part -> linked id
    has_detail: bool = False
    transcript_link: str | None = None  # Drive webViewLink, if recorded
    doc_links: dict[str, str] = field(default_factory=dict)  # label -> Drive link
    docs_on_disk: bool = False  # meeting has document files under DOCUMENTS_DIR


@dataclass
class TypeReport:
    meeting_type: MeetingType
    rows: dict[str, MeetingRow]


def _fetch_uploads(service: Any) -> list[tuple[str, str]]:
    """Every (title, video_id) on the authenticated channel, all pages, deduped
    by id (the uploads listing can return the same video more than once)."""
    resp = service.channels().list(mine=True, part="contentDetails").execute()
    uploads = resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    seen: dict[str, str] = {}  # video_id -> title
    request = service.playlistItems().list(
        playlistId=uploads, part="snippet", maxResults=RESULT_COUNT
    )
    pages = 0
    while request is not None and pages < MAX_UPLOADS_PAGES:
        response = request.execute()
        for item in response["items"]:
            snippet = item["snippet"]
            seen.setdefault(snippet["resourceId"]["videoId"], snippet["title"])
        request = service.playlistItems().list_next(request, response)
        pages += 1
    return [(title, vid) for vid, title in seen.items()]


def _youtube_service() -> Any:
    creds = load_credentials(
        scopes=SCOPES,
        client_secret_path=CLIENT_SECRETS_FILE,
        token_path=YOUTUBE_TOKEN_FILE,
    )
    return build("youtube", "v3", credentials=creds)


def _drive_service() -> Any:
    creds = load_credentials(
        scopes=DRIVE_SCOPES,
        client_secret_path=DESKTOP_APP_CLIENT_SECRET,
        token_path=DRIVE_TOKEN_FILE,
    )
    return build("drive", "v3", credentials=creds)


_DRIVE_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]+)")


def _drive_file_id(link: str) -> str | None:
    """Extract the Drive/Docs file id from a webViewLink, or None.

    Handles both Docs (``docs.google.com/document/d/<id>/edit``) and uploaded
    files (``drive.google.com/file/d/<id>/view``).
    """
    match = _DRIVE_ID_RE.search(link)
    return match.group(1) if match else None


def live_drive_ids(service: Any, ids: set[str]) -> set[str]:
    """The subset of ``ids`` that still resolve to a live (non-trashed) Drive file.

    One ``files.get`` per distinct id; a 404 (or any error) or a trashed file
    counts as dead. Ids are already deduped by the caller to respect quota.
    """
    live: set[str] = set()
    for file_id in ids:
        try:
            meta = (
                service.files()
                .get(fileId=file_id, fields="id, trashed", supportsAllDrives=True)
                .execute()
            )
        except Exception:
            continue  # 404 / permission / gone -> dead
        if not meta.get("trashed"):
            live.add(file_id)
    return live


def _all_drive_ids() -> set[str]:
    """Every Drive file id referenced by a transcript_link or doc_link in Redis,
    deduped, so liveness is checked with one ``files.get`` per distinct file."""
    ids: set[str] = set()
    for raw_key in r.scan_iter(match="transcript_link.*"):
        val = r.get(raw_key)
        if val:
            file_id = _drive_file_id(val.decode("utf-8"))
            if file_id:
                ids.add(file_id)
    for raw_key in r.scan_iter(match="doc_link.*"):
        for val in (r.hgetall(raw_key) or {}).values():
            decoded = val.decode("utf-8") if isinstance(val, bytes) else val
            file_id = _drive_file_id(decoded)
            if file_id:
                ids.add(file_id)
    return ids


def _disk_index() -> tuple[dict[str, dict[str, set[str]]], dict[str, int]]:
    """Scan the stage dirs once. Returns (by_stub, unrecognized_counts).

    ``by_stub[file_stub][meeting_key] = {stage labels}`` and, for compressed
    files, the parts are folded into the key via the caller. ``unrecognized`` is
    per-stage counts of files that matched no known stub or carried no date.
    """
    # Collected per stub so each type can pull its own; a file belongs to the
    # stub that appears in its name (stubs are mutually non-overlapping).
    stubs = {mt.file_stub: mt for mt in MEETING_TYPE_BY_SOURCE.values()}
    by_stub: dict[str, dict[str, set[str]]] = {stub: defaultdict(set) for stub in stubs}
    # Track compressed parts separately: stub -> key -> {parts}
    unrecognized: dict[str, int] = defaultdict(int)
    for stage, directory in STAGE_DIRS.items():
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.startswith("._") or not os.path.isfile(
                os.path.join(directory, name)
            ):
                continue
            stub = next((s for s in stubs if s in name), None)
            key = _meeting_key_from_filename(name)
            if not stub or not key:
                unrecognized[stage] += 1
                continue
            by_stub[stub][key].add(stage)
    return by_stub, unrecognized


def _compressed_parts_by_stub() -> dict[str, dict[str, set[int]]]:
    """stub -> meeting_key -> {compressed part numbers on disk}."""
    stubs = [mt.file_stub for mt in MEETING_TYPE_BY_SOURCE.values()]
    out: dict[str, dict[str, set[int]]] = {s: defaultdict(set) for s in stubs}
    if not os.path.isdir(COMPRESSED_DIR):
        return out
    for name in os.listdir(COMPRESSED_DIR):
        if name.startswith("._") or not name.endswith(".mp4"):
            continue
        stub = next((s for s in stubs if s in name), None)
        key = _meeting_key_from_filename(name)
        if stub and key:
            out[stub][key].add(get_part_num_from_string(name))
    return out


def _documents_on_disk() -> dict[str, set[str]]:
    """stub -> {meeting keys that have a non-empty document folder on disk}.

    Document folders are named ``<file_stub> <meeting_key>`` under DOCUMENTS_DIR
    (see helpers.document_store.meeting_document_dir).
    """
    stubs = [mt.file_stub for mt in MEETING_TYPE_BY_SOURCE.values()]
    out: dict[str, set[str]] = {s: set() for s in stubs}
    if not os.path.isdir(DOCUMENTS_DIR):
        return out
    for name in os.listdir(DOCUMENTS_DIR):
        folder = os.path.join(DOCUMENTS_DIR, name)
        if name.startswith("._") or not os.path.isdir(folder):
            continue
        stub = next((s for s in stubs if name.startswith(f"{s} ")), None)
        key = _meeting_key_from_filename(name)
        if stub and key and any(not f.startswith("._") for f in os.listdir(folder)):
            out[stub].add(key)
    return out


def build_type_report(
    meeting_type: MeetingType,
    channel_ids: set[str],
    uploads: list[tuple[str, str]],
    disk_by_stub: dict[str, dict[str, set[str]]],
    compressed_parts: dict[str, dict[str, set[int]]],
    docs_by_stub: dict[str, set[str]],
) -> TypeReport:
    rows: dict[str, MeetingRow] = {}

    def row(key: str) -> MeetingRow:
        return rows.setdefault(key, MeetingRow(key=key))

    # --- disk ---
    for key, stages in disk_by_stub.get(meeting_type.file_stub, {}).items():
        row(key).on_disk |= stages
    for key, parts in compressed_parts.get(meeting_type.file_stub, {}).items():
        row(key).disk_parts |= parts
    for key in docs_by_stub.get(meeting_type.file_stub, set()):
        row(key).docs_on_disk = True

    # --- youtube (this type's titles) ---
    for title, vid in uploads:
        if meeting_type.compressed_title_prefix not in title:
            continue
        key = get_date_or_datetime_string_from_string(title)
        if key:  # every id in `uploads` is live by construction
            row(key).yt_parts[get_part_num_from_string(title)] = vid

    # --- redis: detail ---
    detail = r.hgetall(meeting_type.redis_key(DETAIL_KEY)) or {}
    for field_bytes in detail:
        key = (
            field_bytes.decode("utf-8")
            if isinstance(field_bytes, bytes)
            else field_bytes
        )
        row(key).has_detail = True

    # --- redis: video_link.* ---
    prefix = f"video_link.{meeting_type.key}."
    for raw_key in r.scan_iter(match=f"{prefix}*"):
        rk = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
        # video_link.<type>.<meeting_key>.<part>
        rest = rk[len(prefix) :]
        meeting_key, _, part_str = rest.rpartition(".")
        if not meeting_key or not part_str.isdigit():
            continue
        val = r.get(rk)
        if not val:
            continue
        vid = _video_id_from_link(val.decode("utf-8"))
        row(meeting_key).link_parts[int(part_str)] = vid

    # --- redis: transcript_link.<type>.<meeting_key> -> Drive webViewLink ---
    tprefix = f"transcript_link.{meeting_type.key}."
    for raw_key in r.scan_iter(match=f"{tprefix}*"):
        rk = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
        val = r.get(rk)
        if val:
            row(rk[len(tprefix) :]).transcript_link = val.decode("utf-8")

    # --- redis: doc_link.<type>.<meeting_key> -> hash of {label: Drive link} ---
    dprefix = f"doc_link.{meeting_type.key}."
    for raw_key in r.scan_iter(match=f"{dprefix}*"):
        rk = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
        links = {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (
                v.decode("utf-8") if isinstance(v, bytes) else v
            )
            for k, v in (r.hgetall(rk) or {}).items()
        }
        if links:
            row(rk[len(dprefix) :]).doc_links = links

    return TypeReport(meeting_type=meeting_type, rows=rows)


@dataclass
class Scope:
    """Which publishing destinations reconcile checks and acts on.

    A destination that is disabled in the pipeline config (DISABLED_PUBLISHERS) is
    out of scope: its findings are not reported and its reconcile actions are not
    taken, so a disk-only or wiki-less install shows no spurious mismatches.
    """

    youtube: bool
    google_docs: bool
    wiki: bool

    @classmethod
    def from_config(cls) -> "Scope":
        return cls(
            youtube=is_destination_enabled("youtube"),
            google_docs=is_destination_enabled("google_docs"),
            wiki=is_destination_enabled("wiki"),
        )

    def summary(self) -> str:
        on = [
            label
            for label, enabled in (
                ("youtube", self.youtube),
                ("google_docs", self.google_docs),
                ("wiki", self.wiki),
            )
            if enabled
        ]
        off = [
            label
            for label, enabled in (
                ("youtube", self.youtube),
                ("google_docs", self.google_docs),
                ("wiki", self.wiki),
            )
            if not enabled
        ]
        return (
            f"in scope: {', '.join(on) or 'none'}; disabled: {', '.join(off) or 'none'}"
        )


def _has_presence(m: MeetingRow) -> bool:
    """The meeting actually exists in this type: it has a detail record or files
    on disk. A row with only link/upload entries and no presence is contamination
    (a pointer misfiled under this namespace by a past cross-type bug)."""
    return m.has_detail or bool(m.on_disk) or bool(m.disk_parts) or m.docs_on_disk


def find_mismatches(
    report: TypeReport,
    channel_ids: set[str],
    drive_ids: set[str],
    include_historical: bool,
    scope: Scope,
) -> tuple[list[str], int, int]:
    """Return (actionable mismatch lines, orphan-link count, historical-skipped).

    Only checks for destinations in ``scope`` are reported: with YouTube disabled
    the video/upload findings vanish, and with Google Docs disabled the transcript
    finding does. Orphan links (dead ``video_link`` pointers with no matching
    meeting) are a YouTube concern and counted only when YouTube is in scope.
    Meetings older than the pipeline floor (EARLIEST) are pre-pipeline archives and
    are skipped (counted) unless ``include_historical``.
    """
    lines: list[str] = []
    orphan_links = 0
    historical = 0
    for key in sorted(report.rows, reverse=True):
        m = report.rows[key]

        if not _has_presence(m):
            # Count individual dead pointers (parts), matching what reconcile
            # deletes — a live-pointing misfiling is harmless and left alone.
            if scope.youtube:
                orphan_links += sum(
                    1 for vid in m.link_parts.values() if vid not in channel_ids
                )
            continue

        dt = _key_datetime(key)
        if not include_historical and dt is not None and dt < EARLIEST:
            historical += 1
            continue

        issues: list[str] = []

        if scope.youtube:
            # STALE_VIDEO_LINK: a pointer to a video no longer on the channel —
            # this is what breaks the wiki "Video Backup" links.
            stale = {p: v for p, v in m.link_parts.items() if v not in channel_ids}
            if stale:
                issues.append(
                    "STALE_VIDEO_LINK "
                    + ", ".join(f"part {p}->{v}" for p, v in sorted(stale.items()))
                )

            # MISSING_VIDEO_LINK: a live upload with no pointer to it.
            missing_link = sorted(set(m.yt_parts) - set(m.link_parts))
            if missing_link:
                issues.append(
                    "MISSING_VIDEO_LINK parts " + ", ".join(map(str, missing_link))
                )

            # MISSING_UPLOAD: a compressed part on disk that isn't on YouTube.
            missing_upload = sorted(m.disk_parts - set(m.yt_parts))
            if missing_upload:
                issues.append(
                    "MISSING_UPLOAD parts " + ", ".join(map(str, missing_upload))
                )

        # NO_DETAIL: on disk or YouTube but not in the detail hash (not tied to a
        # single destination).
        if not m.has_detail and (m.on_disk or m.yt_parts):
            issues.append("NO_DETAIL (on disk/youtube but not in Redis detail)")

        if scope.google_docs:
            # MISSING_TRANSCRIPT: a transcript file on disk with no Drive pointer.
            if "transcript" in m.on_disk and not m.transcript_link:
                issues.append("MISSING_TRANSCRIPT (on disk, no Drive link)")
            # STALE_TRANSCRIPT_LINK: the transcript_link points at a Drive file
            # that no longer exists (a broken wiki transcript link).
            elif (
                m.transcript_link and _drive_file_id(m.transcript_link) not in drive_ids
            ):
                issues.append("STALE_TRANSCRIPT_LINK")

            # STALE_DOC_LINK: an archived-document pointer to a dead Drive file.
            stale_docs = sorted(
                label
                for label, url in m.doc_links.items()
                if _drive_file_id(url) not in drive_ids
            )
            if stale_docs:
                issues.append("STALE_DOC_LINK " + ", ".join(stale_docs))

            # MISSING_DOC: documents on disk but nothing archived to Drive.
            if m.docs_on_disk and not m.doc_links:
                issues.append("MISSING_DOC (on disk, none archived to Drive)")

        if issues:
            lines.append(f"  {key}: " + "; ".join(issues))
    return lines, orphan_links, historical


def _affected_real_meetings(
    report: TypeReport, channel_ids: set[str], include_historical: bool
) -> set[str]:
    """Meeting keys that genuinely exist in this type (detail/disk) and have a
    stale or missing video_link — the real broken wiki backups worth re-rendering.

    Honors the EARLIEST floor: a pre-pipeline meeting must NOT be flagged for
    re-render, because WikiUpdater removes a flagged section unconditionally and
    then declines to re-add anything older than EARLIEST — which would silently
    drop that meeting's row from the wiki.
    """
    affected: set[str] = set()
    for key, m in report.rows.items():
        if not _has_presence(m):
            continue
        dt = _key_datetime(key)
        if not include_historical and dt is not None and dt < EARLIEST:
            continue
        has_stale = any(v not in channel_ids for v in m.link_parts.values())
        has_missing = bool(set(m.yt_parts) - set(m.link_parts))
        if has_stale or has_missing:
            affected.add(key)
    return affected


def _stale_drive(
    report: TypeReport, drive_ids: set[str], include_historical: bool
) -> tuple[set[str], int, int]:
    """Real, in-floor meetings with a dead Drive pointer.

    Returns (affected meeting keys, #stale transcript links, #stale doc entries).
    A pointer is dead when its Drive file id is not among the live ``drive_ids``.
    """
    affected: set[str] = set()
    stale_transcripts = 0
    stale_docs = 0
    for key, m in report.rows.items():
        if not _has_presence(m):
            continue
        dt = _key_datetime(key)
        if not include_historical and dt is not None and dt < EARLIEST:
            continue
        touched = False
        if m.transcript_link and _drive_file_id(m.transcript_link) not in drive_ids:
            stale_transcripts += 1
            touched = True
        dead = [u for u in m.doc_links.values() if _drive_file_id(u) not in drive_ids]
        if dead:
            stale_docs += len(dead)
            touched = True
        if touched:
            affected.add(key)
    return affected, stale_transcripts, stale_docs


def reconcile_type(
    source_type: SourceType,
    report: TypeReport,
    channel_ids: set[str],
    drive_ids: set[str],
    include_historical: bool,
    scope: Scope,
) -> list[str]:
    """Fix what can be fixed by driving existing workflows. Returns action log.

    Only in-scope destinations are acted on: video_link repointing/cleanup runs
    only when YouTube is enabled, dead Drive pointers are cleared only when Google
    Docs is enabled, and the wiki re-render flag is set only when the wiki is.
    """
    actions: list[str] = []
    mt = report.meeting_type
    wiki_flagged: set[str] = set()

    def flag_wiki(keys: set[str]) -> None:
        if scope.wiki:
            for key in sorted(keys):
                r.sadd(mt.redis_key(WIKI_REFRESH_KEY), key)
            wiki_flagged.update(keys)

    # video_link repointing/cleanup is a YouTube-destination concern.
    if scope.youtube:
        affected = _affected_real_meetings(report, channel_ids, include_historical)
        has_dead = any(
            v not in channel_ids
            for m in report.rows.values()
            for v in m.link_parts.values()
        )

        # 1) Repoint every video_link for this type via the uploader's own
        #    workflow (rewrites links for all live videos, healing staleness).
        if affected:
            uploader = VideoUploader(source_type)
            uploads_playlist = uploader.get_my_uploads_list()
            if uploads_playlist:
                uploader.get_recent_video_titles(uploads_playlist)
                actions.append(f"repointed video_link.{mt.key}.* from live uploads")

        # 2) Delete pointers with no surviving video — meetings whose only copy
        #    was deleted and orphan contamination misfiled under this namespace.
        if has_dead:
            prefix = f"video_link.{mt.key}."
            deleted = 0
            for raw_key in list(r.scan_iter(match=f"{prefix}*")):
                rk = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
                val = r.get(rk)
                if val and _video_id_from_link(val.decode("utf-8")) not in channel_ids:
                    r.delete(rk)
                    deleted += 1
            if deleted:
                actions.append(f"deleted {deleted} dead/orphan video_link pointer(s)")

        # 3) Flag real affected meetings for wiki re-render (see flag_wiki: only
        #    when the wiki is a destination).
        flag_wiki(affected)

    # dead Drive pointers (transcripts + archived docs) are a Google-Docs concern.
    if scope.google_docs:
        drive_affected, _, _ = _stale_drive(report, drive_ids, include_historical)
        cleared_t = 0
        cleared_d = 0
        for key in drive_affected:
            m = report.rows[key]
            if m.transcript_link and _drive_file_id(m.transcript_link) not in drive_ids:
                r.delete(mt.transcript_link_key_template.format(meeting_key=key))
                cleared_t += 1
            dead_labels = [
                label
                for label, url in m.doc_links.items()
                if _drive_file_id(url) not in drive_ids
            ]
            if dead_labels:
                r.hdel(
                    mt.doc_link_key_template.format(meeting_key=key),
                    *dead_labels,
                )
                cleared_d += len(dead_labels)
        if cleared_t:
            actions.append(f"cleared {cleared_t} dead transcript_link pointer(s)")
        if cleared_d:
            actions.append(f"cleared {cleared_d} dead doc_link entry(ies)")
        flag_wiki(drive_affected)

    if wiki_flagged:
        actions.append(
            f"flagged {len(wiki_flagged)} meeting(s) for wiki refresh "
            f"({mt.redis_key(WIKI_REFRESH_KEY)})"
        )
    return actions


@dataclass
class Plan:
    """What ``--reconcile`` would change, aggregated for the dry-run summary."""

    repoint: int = 0  # pointers that would be re-aimed at a surviving video
    clear_real: int = 0  # dead pointers on real meetings with no surviving video
    clear_orphan: int = 0  # orphan contamination pointers with no meeting behind them
    clear_transcript: int = 0  # transcript_link pointers to a dead Drive file
    clear_doc: int = 0  # archived-document pointers to a dead Drive file
    wiki_keys: set[str] = field(default_factory=set)  # real meetings to re-render
    pages: set[str] = field(default_factory=set)  # wiki pages those meetings live on

    def merge(self, other: "Plan") -> None:
        self.repoint += other.repoint
        self.clear_real += other.clear_real
        self.clear_orphan += other.clear_orphan
        self.clear_transcript += other.clear_transcript
        self.clear_doc += other.clear_doc
        self.wiki_keys |= other.wiki_keys
        self.pages |= other.pages

    @property
    def total_deletes(self) -> int:
        return self.clear_real + self.clear_orphan

    @property
    def any_changes(self) -> bool:
        return bool(
            self.repoint
            or self.total_deletes
            or self.clear_transcript
            or self.clear_doc
            or self.wiki_keys
        )


def plan_for_report(
    report: TypeReport,
    channel_ids: set[str],
    drive_ids: set[str],
    include_historical: bool,
    scope: Scope,
) -> Plan:
    """Compute what --reconcile would do for one type (no side effects).

    Mirrors reconcile_type's scoping: with YouTube disabled there are no pointer
    changes, and with the wiki disabled no meetings are flagged for re-render.
    """
    plan = Plan()
    mt = report.meeting_type

    def flag_wiki(key: str) -> None:
        if scope.wiki:
            plan.wiki_keys.add(key)
            plan.pages.add(
                mt.wiki_year_template.format(get_year_string_from_string(key))
            )

    if scope.youtube:
        for key, m in report.rows.items():
            if _has_presence(m):
                dt = _key_datetime(key)
                historical = not include_historical and dt is not None and dt < EARLIEST
                touched = False
                for part, vid in m.link_parts.items():
                    if vid in channel_ids:
                        continue
                    if part in m.yt_parts:  # a surviving upload to re-aim at
                        plan.repoint += 1
                    else:  # nothing live to point at -> the pointer is removed
                        plan.clear_real += 1
                    touched = True
                for _ in set(m.yt_parts) - set(m.link_parts):  # uploaded, never linked
                    plan.repoint += 1
                    touched = True
                if touched and not historical:
                    flag_wiki(key)
            else:
                for vid in m.link_parts.values():
                    if vid not in channel_ids:
                        plan.clear_orphan += 1

    if scope.google_docs:
        affected, stale_t, stale_d = _stale_drive(report, drive_ids, include_historical)
        plan.clear_transcript += stale_t
        plan.clear_doc += stale_d
        for key in affected:
            flag_wiki(key)

    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="after auditing, repoint/clear links and flag wiki re-render",
    )
    parser.add_argument(
        "--type",
        dest="type_key",
        default=None,
        help="limit to one meeting-type key (e.g. cc_mtg, pc_mtg)",
    )
    parser.add_argument(
        "--all",
        dest="include_historical",
        action="store_true",
        help=f"include pre-pipeline meetings (older than {EARLIEST:%Y-%m-%d})",
    )
    args = parser.parse_args()

    scope = Scope.from_config()
    # Only reach out to a destination when it is configured — a disk-only install
    # has no YouTube/Drive credentials to authenticate with.
    if scope.youtube:
        service = _youtube_service()
        uploads = _fetch_uploads(service)
        channel_ids = {vid for _, vid in uploads}
    else:
        uploads = []
        channel_ids = set()
    if scope.google_docs:
        drive_ids = live_drive_ids(_drive_service(), _all_drive_ids())
    else:
        drive_ids = set()
    disk_by_stub, unrecognized = _disk_index()
    compressed_parts = _compressed_parts_by_stub()
    docs_by_stub = _documents_on_disk()

    mode = "RECONCILE (writes to Redis)" if args.reconcile else "AUDIT (read-only)"
    print(f"Reconciliation report — mode: {mode}")
    print(f"Destinations {scope.summary()}.")
    channel_note = (
        f"{len(channel_ids)} live channel video(s)"
        if scope.youtube
        else "YouTube (disabled — skipped)"
    )
    drive_note = (
        f"{len(drive_ids)} live Drive file(s)"
        if scope.google_docs
        else "Google Drive (disabled — skipped)"
    )
    print(
        f"Cross-checking {channel_note} and {drive_note} against the files on disk "
        "and the pointers in Redis, per meeting type.\n"
    )
    print(
        "What the findings mean:\n"
        "  STALE_VIDEO_LINK   Redis points a meeting's 'Video Backup' at a video "
        "that is no longer on the channel — the wiki link is broken.\n"
        "  MISSING_VIDEO_LINK a video is uploaded but no Redis pointer references "
        "it, so the wiki shows no backup link for that part.\n"
        "  MISSING_UPLOAD     a compressed video part is on disk but was never "
        "uploaded to YouTube.\n"
        "  NO_DETAIL          the meeting has files/uploads but no Redis 'detail' "
        "record (usually an older meeting already on the wiki; informational).\n"
        "  MISSING_TRANSCRIPT a transcript file is on disk but no Drive link is set.\n"
        "  STALE_TRANSCRIPT_LINK the transcript_link points at a Drive file that no "
        "longer exists — the wiki transcript link is broken.\n"
        "  MISSING_DOC        documents are on disk but none are archived to Drive.\n"
        "  STALE_DOC_LINK     an archived-document pointer references a dead Drive "
        "file.\n"
        "  ORPHAN_LINK        a dead pointer with no matching meeting in this type — "
        "leftover cross-type contamination; never shown on the wiki.\n"
    )

    total_issue_meetings = 0
    total_orphan_links = 0
    total_historical = 0
    total_plan = Plan()
    for source_type, meeting_type in MEETING_TYPE_BY_SOURCE.items():
        if args.type_key and meeting_type.key != args.type_key:
            continue
        report = build_type_report(
            meeting_type,
            channel_ids,
            uploads,
            disk_by_stub,
            compressed_parts,
            docs_by_stub,
        )
        lines, orphan_links, historical = find_mismatches(
            report, channel_ids, drive_ids, args.include_historical, scope
        )
        total_orphan_links += orphan_links
        total_historical += historical
        total_plan.merge(
            plan_for_report(
                report, channel_ids, drive_ids, args.include_historical, scope
            )
        )
        header = f"=== {meeting_type.display_name} ({meeting_type.key}) ==="
        if lines or orphan_links:
            total_issue_meetings += len(lines)
            print(header)
            if lines:
                print("\n".join(lines))
            if orphan_links:
                print(
                    f"  ORPHAN_LINK: {orphan_links} dead pointer(s) with no matching "
                    "meeting in this type (contamination; cleanup only, not on wiki)"
                )
            if args.reconcile:
                for action in reconcile_type(
                    source_type,
                    report,
                    channel_ids,
                    drive_ids,
                    args.include_historical,
                    scope,
                ):
                    print(f"    -> {action}")
            print()

    unrecognized_total = sum(unrecognized.values())
    if unrecognized_total:
        print(
            f"Note: {unrecognized_total} file(s) under the stage directories were "
            "left out because their names carry no recognized date or meeting-type "
            "stub (e.g. legacy pre-pipeline names): "
            + ", ".join(f"{stage}={n}" for stage, n in sorted(unrecognized.items()))
            + ".\n"
        )

    if total_historical:
        print(
            f"Note: {total_historical} pre-pipeline meeting(s) dated before "
            f"{EARLIEST:%Y-%m-%d} were skipped (they predate the automated pipeline "
            "and are left untouched). Pass --all to include them.\n"
        )

    if not total_issue_meetings and not total_orphan_links:
        print(
            "Everything agrees — disk, YouTube, and Redis are consistent. Nothing to do."
        )
        return

    print(
        f"Summary: {total_issue_meetings} meeting(s) flagged with findings above "
        "(some, like NO_DETAIL, are informational only), plus "
        f"{total_orphan_links} orphan contamination pointer(s). The actionable "
        "subset is spelled out next.\n"
    )
    _print_planned_changes(total_plan, applied=args.reconcile)


def _print_planned_changes(plan: Plan, applied: bool) -> None:
    """Describe, in prose, exactly what --reconcile changes — and what it never
    touches — separating internal Redis writes from the one outward-facing effect."""
    verb = "Applied" if applied else "Would apply"
    lead = (
        "The changes below have been written to Redis"
        if applied
        else "Running again with --reconcile would make the following changes"
    )
    print(f"{lead}:\n")

    print("  Redis (internal pipeline state — not visible to anyone directly):")
    if plan.repoint:
        print(
            f"    • {verb.lower()}: re-aim {plan.repoint} video-backup pointer(s) at "
            "the surviving re-uploaded video(s), via the uploader's own "
            "get_recent_video_titles workflow."
        )
    if plan.clear_real:
        print(
            f"    • {verb.lower()}: remove {plan.clear_real} pointer(s) on real "
            "meetings whose video is gone with no surviving copy to point at."
        )
    if plan.clear_orphan:
        print(
            f"    • {verb.lower()}: remove {plan.clear_orphan} orphan pointer(s) "
            "left by past cross-type contamination (no meeting behind them)."
        )
    if plan.clear_transcript:
        print(
            f"    • {verb.lower()}: clear {plan.clear_transcript} transcript_link "
            "pointer(s) to a Drive file that no longer exists (re-uploaded on the "
            "next pipeline run)."
        )
    if plan.clear_doc:
        print(
            f"    • {verb.lower()}: clear {plan.clear_doc} archived-document "
            "pointer(s) to a dead Drive file."
        )
    if not (
        plan.repoint or plan.total_deletes or plan.clear_transcript or plan.clear_doc
    ):
        print("    • (no pointer changes)")

    print("\n  Public wiki (the ONLY outward-facing change):")
    if plan.wiki_keys:
        pages = ", ".join(f'"{p}"' for p in sorted(plan.pages))
        tail = (
            "have been flagged; the next WikiUpdater run will rewrite them"
            if applied
            else "would be flagged so the next WikiUpdater run rewrites them"
        )
        print(
            f"    • {len(plan.wiki_keys)} meeting row(s) {tail}. Their broken "
            "backup/transcript/document links get repointed or removed. "
            f"Affected page(s): {pages}."
        )
        print(
            "    • Note: this script only sets a Redis flag; the actual wiki edit "
            "happens on the next scheduled (or manually triggered) WikiUpdater run, "
            "not right now."
        )
    else:
        print("    • None — no wiki rows are re-rendered.")

    print(
        "\n  Never touched: YouTube (no uploads/deletes), Google Drive, and the "
        "files on disk. This tool only reads those; it writes to Redis alone.\n"
    )
    if not applied:
        print("Re-run with --reconcile to apply the Redis changes above.")


if __name__ == "__main__":
    main()
