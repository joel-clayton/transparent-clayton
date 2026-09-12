"""Fetch Clayton meetings from the CivicClerk OData API.

The portal (``claytonca.portal.civicclerk.com``) is a thin SPA over a public,
no-auth OData API at ``claytonca.api.civicclerk.com/v1/``. The SPA's default view
requests only ``startDateTime ge <today>`` — *upcoming* events — so the old DOM
scraper structurally could never see past meetings in a lookback range. Querying
the API with an explicit date range returns every event (all types, all assets)
reliably and without driving a browser.

This module is the pure, network-narrow layer: fetching + turning a raw event
dict into the pieces a :class:`~src.scrapers.models.MeetingRecord` needs. The
idempotency / snapshot / quarantine orchestration lives in
:func:`src.scrapers.cc_meetings.parse_meetings_from_civic_clerk_iframe`, which
calls into here. Kept dependency-free of ``celery_app``/Redis so it stays unit
-testable and cannot form an import cycle with its caller.

Event fields relied on: ``id``, ``categoryName``, ``startDateTime``,
``isOnDemandEvent``, ``mediaStreamPath``, ``publishedFiles[]`` (``fileId``,
``type``, ``name``). Derived asset URLs are documented on the helpers below.
"""

import logging
from datetime import datetime
from typing import Any

import requests

from src.meeting_types import MeetingType
from src.scrapers.constants import (
    CIVIC_CLERK_API_MAX_PAGES,
    CIVIC_CLERK_API_TIMEOUT,
    CIVIC_CLERK_EVENTS_ENDPOINT,
    CIVIC_CLERK_FILE_STREAM_TEMPLATE,
    CIVIC_CLERK_MEDIA_BASE,
)
from src.scrapers.errors import TransientScrapeError

logger = logging.getLogger(__name__)

# The API stores startDateTime as wall-clock local time with a 'Z' suffix, so we
# format the range bounds the same way and compare as naive local time.
API_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
AGENDA_PACKET_TYPE = "Agenda Packet"


def _range_filter(start: datetime, end: datetime) -> str:
    """OData ``$filter`` for events strictly between ``start`` and ``end``.

    Uses ``gt``/``lt`` to mirror the scraper's strictly-after-watermark,
    strictly-before-now semantics; the caller re-applies the same bounds
    client-side so this is only a fetch-narrowing optimization.
    """
    return (
        f"startDateTime gt {start.strftime(API_DATETIME_FORMAT)} "
        f"and startDateTime lt {end.strftime(API_DATETIME_FORMAT)}"
    )


def fetch_events(
    start: datetime, end: datetime, *, session: Any = None
) -> list[dict[str, Any]]:
    """All events between ``start`` and ``end``, following ``@odata.nextLink``.

    Raises :class:`TransientScrapeError` on any network-level failure so the
    caller can retry/alert rather than treat an outage as "no meetings".
    """
    client = session or requests
    url: str | None = CIVIC_CLERK_EVENTS_ENDPOINT
    # nextLink carries its own encoded query, so params apply to the first page
    # only and are dropped thereafter.
    params: dict[str, str] | None = {
        "$filter": _range_filter(start, end),
        "$orderby": "startDateTime asc",
    }
    events: list[dict[str, Any]] = []
    try:
        for _ in range(CIVIC_CLERK_API_MAX_PAGES):
            response = client.get(
                url,
                params=params,
                timeout=CIVIC_CLERK_API_TIMEOUT,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            body = response.json()
            events.extend(body.get("value", []))
            url = body.get("@odata.nextLink")
            params = None
            if not url:
                break
        else:
            logger.warning(
                "Stopped following CivicClerk pagination at the %d-page cap",
                CIVIC_CLERK_API_MAX_PAGES,
            )
    except requests.RequestException as exc:
        raise TransientScrapeError(f"CivicClerk API request failed: {exc}") from exc
    return events


def event_datetime(event: dict[str, Any]) -> datetime:
    """Naive local meeting datetime (raises ``ValueError`` if unparseable)."""
    raw = str(event.get("startDateTime") or "")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


def matches_category(event: dict[str, Any], meeting_type: MeetingType) -> bool:
    """Whether ``event`` belongs to ``meeting_type`` (exact categoryName match)."""
    return (event.get("categoryName") or "") == meeting_type.category


def is_cancelled(event: dict[str, Any]) -> bool:
    """Whether the city marked this meeting cancelled.

    The portal signals a cancellation by prefixing the ``agendaName`` with
    "CANCELED"/"Canceled" (spelling and case vary), e.g. "CANCELED - Planning
    Commission Meeting". A cancelled meeting still publishes a cancellation
    notice, so it otherwise looks like a docs-only meeting.
    """
    return (event.get("agendaName") or "").strip().lower().startswith("cancel")


def video_url(event: dict[str, Any]) -> str:
    """The playable MP4 URL for an on-demand event, or ``""`` if none.

    ``mediaStreamPath`` comes in two shapes across events: a CDN-relative path
    ("CLAYTONCA/<guid>.mp4", which maps onto the media CDN base once lowercased)
    or an already-absolute URL. Return absolute paths untouched; only the
    relative form is joined + lowercased.
    """
    if not event.get("isOnDemandEvent"):
        return ""
    stream_path = str(event.get("mediaStreamPath") or "").strip()
    if not stream_path:
        return ""
    if stream_path.lower().startswith(("http://", "https://")):
        return stream_path
    return CIVIC_CLERK_MEDIA_BASE + stream_path.lower()


def split_documents(event: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Return ``(agenda_packet_url, {label: url})`` from an event's files.

    The "Agenda Packet" file (if any) is separated out to match the scraped
    record shape :func:`src.processors.archive_docs.docs_to_archive` expects;
    every other published file is keyed by its name into the supplemental map.
    URLs are the stable ``GetMeetingFileStream`` endpoint (no expiry).
    """
    agenda_packet = ""
    supplemental: dict[str, str] = {}
    for published in event.get("publishedFiles") or []:
        file_id = published.get("fileId")
        if not file_id:
            continue
        doc_url = CIVIC_CLERK_FILE_STREAM_TEMPLATE.format(file_id=file_id)
        if (published.get("type") or "") == AGENDA_PACKET_TYPE and not agenda_packet:
            agenda_packet = doc_url
            continue
        label = str(
            published.get("name") or published.get("type") or f"File {file_id}"
        ).strip()
        # Distinct files can share a name; suffix duplicates so none are lost.
        key, suffix = label, 2
        while key in supplemental:
            key = f"{label} ({suffix})"
            suffix += 1
        supplemental[key] = doc_url
    return agenda_packet, supplemental


def merge_by_datetime(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine same-datetime events into one synthetic event per meeting.

    The portal represents a single meeting with more than one event record at
    the same time: sometimes an empty placeholder alongside the real one, and
    sometimes the assets *split* across records (one carries the video, another
    the documents — e.g. 2026-09-01 City Council). Picking one record would drop
    the other's assets, so we union each group instead.

    The video-bearing record (if any) is the base — its ``id`` anchors the A/V
    idempotency key and its media fields carry the video — and every record's
    ``publishedFiles`` are unioned onto it (deduped by ``fileId``). The result is
    still an event-shaped dict, so :func:`video_url` / :func:`split_documents` /
    :func:`event_datetime` consume it unchanged.
    """
    groups: dict[datetime, list[dict[str, Any]]] = {}
    for event in events:
        try:
            when = event_datetime(event)
        except ValueError:
            continue
        groups.setdefault(when, []).append(event)

    merged: list[dict[str, Any]] = []
    for when in sorted(groups):
        group = groups[when]
        base = next((event for event in group if video_url(event)), group[0])
        combined = dict(base)
        files_by_id: dict[Any, dict[str, Any]] = {}
        for event in group:
            for published in event.get("publishedFiles") or []:
                file_id = published.get("fileId")
                if file_id and file_id not in files_by_id:
                    files_by_id[file_id] = published
        combined["publishedFiles"] = list(files_by_id.values())
        merged.append(combined)
    return merged
