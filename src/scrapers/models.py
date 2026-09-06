"""Validated output contract for the meeting scraper.

``MeetingRecord`` is the typed record the scraper produces. Its field names and
JSON keys are identical to the ``Meeting`` TypedDict in :mod:`src.types`, so
``model_dump(mode="json")`` round-trips through Redis unchanged and downstream
consumers keep reading plain dicts (``.get(...)``/subscript). The extra metadata
fields (``scraped_at``, ``snapshot_ref``, ``status``) are additive; older
records that predate them still load because unknown keys are ignored.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.constants import DATE_FORMAT, DATETIME_FORMAT

# The literal stored in every record's ``source_type`` today; kept as a plain
# string (not the int-valued ``SourceType`` enum in src.types) so the wire shape
# is byte-for-byte identical to what downstream already reads.
CITY_COUNCIL_MEETING_SOURCE = "city_council_meeting"


class ScrapeStatus(str, Enum):
    """Lifecycle of a scraped record.

    ``str``-valued so ``model_dump(mode="json")`` serialises to a bare string.
    """

    COMPLETE = "complete"  # resolvable video present -> eligible for handoff
    PENDING_VIDEO = "pending_video"  # parsed OK but no video yet -> dropped upstream
    QUARANTINED = "quarantined"  # failed validation -> never handed off


class MeetingRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # --- required: identity + routing ---
    key: str
    clip_id: str
    source_type: str = CITY_COUNCIL_MEETING_SOURCE

    # --- required-for-handoff: empty video => PENDING_VIDEO, dropped upstream ---
    video: str = ""

    # --- optional: frequently absent or short-lived at the source ---
    agenda: str = ""
    agenda_packet: str = ""
    minutes_and_supplemental_materials: dict[str, str] | None = None
    duration: str = ""  # always "" on CivicClerk; retained for Granicus/compat

    # --- additive metadata (ignored by downstream consumers) ---
    scraped_at: str | None = None
    snapshot_ref: str | None = None
    status: ScrapeStatus = ScrapeStatus.COMPLETE

    @field_validator("key")
    @classmethod
    def _key_must_parse(cls, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            raise ValueError("meeting key is empty")
        for fmt in (DATETIME_FORMAT, DATE_FORMAT):
            try:
                datetime.strptime(candidate, fmt)
                return candidate
            except ValueError:
                continue
        raise ValueError(
            f"meeting key {candidate!r} does not match "
            f"{DATETIME_FORMAT!r} or {DATE_FORMAT!r}"
        )

    @field_validator("clip_id")
    @classmethod
    def _clip_id_required(cls, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            raise ValueError("clip_id is required")
        return candidate

    @field_validator("video", "agenda", "agenda_packet")
    @classmethod
    def _url_shape(cls, value: str) -> str:
        candidate = (value or "").strip()
        if candidate and not candidate.startswith(("http://", "https://")):
            raise ValueError(f"URL {candidate!r} is not an http(s) URL")
        return candidate

    @model_validator(mode="after")
    def _derive_status(self) -> "MeetingRecord":
        # An explicit quarantine wins; otherwise the presence of a video decides
        # whether the record is ready for handoff or still pending one.
        if self.status is not ScrapeStatus.QUARANTINED:
            self.status = (
                ScrapeStatus.COMPLETE if self.video else ScrapeStatus.PENDING_VIDEO
            )
        return self
