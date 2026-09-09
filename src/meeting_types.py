"""One config object per meeting type (Phase 2b).

Today the pipeline is hardwired to City Council in ~7 places (the scraper's title
filter, the ``SourceType`` enum, ``.cc_mtg`` Redis keys, file stubs/templates,
the wiki year page, the YouTube playlist name). ``MeetingType`` collapses all of
those per-type strings into one place so the same machinery can serve any type.

Everything is *derived* from ``display_name`` (plus an explicit short ``key`` for
the Redis/filename namespace), so adding a type is one registry entry. The City
Council instance reproduces the current constants byte-for-byte — see
``tests/test_meeting_types.py`` — so migrating consumers onto it changes no data,
files, wiki pages, or the YouTube channel.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MeetingType:
    key: str  # short namespace slug for Redis keys + filenames, e.g. "cc_mtg"
    display_name: str  # human name, e.g. "City Council"
    title_match: str  # substring matched against a CivicClerk listing title
    category: str = ""  # exact CivicClerk API categoryName; defaults to display_name

    def __post_init__(self) -> None:
        # Most types' API category equals their display name; only where the
        # portal's categoryName differs (e.g. GHAD's full legal name) is it set
        # explicitly. object.__setattr__ because the dataclass is frozen.
        if not self.category:
            object.__setattr__(self, "category", self.display_name)

    # --- identity ---
    @property
    def source_type(self) -> str:
        """Value stored in ``MeetingRecord.source_type`` (e.g. city_council_meeting)."""
        return self.display_name.lower().replace(" ", "_") + "_meeting"

    # --- Redis namespaces ---
    def redis_key(self, prefix: str) -> str:
        """A per-type Redis key, e.g. redis_key("detail") -> "detail.cc_mtg"."""
        return f"{prefix}.{self.key}"

    @property
    def video_link_key_template(self) -> str:
        return f"video_link.{self.key}.{{meeting_key}}.{{part_num}}"

    @property
    def transcript_link_key_template(self) -> str:
        return f"transcript_link.{self.key}.{{meeting_key}}"

    @property
    def doc_link_key_template(self) -> str:
        return f"doc_link.{self.key}.{{meeting_key}}"

    @property
    def video_playlist_key_template(self) -> str:
        return f"video_playlist.{self.key}.{{}}"

    # --- filenames + media titles ---
    @property
    def file_stub(self) -> str:
        return f"{self.display_name} Meeting"

    @property
    def file_template(self) -> str:
        return f"{self.display_name} Meeting {{}} - City of Clayton{{}}"

    @property
    def file_template_yt_dlp(self) -> str:
        return f"{self.display_name} Meeting {{}} - City of Clayton.%(ext)s"

    @property
    def compressed_segmented_template(self) -> str:
        return f"Clayton CA {self.display_name} Meeting {{}} - %03d{{}}"

    @property
    def compressed_not_segmented_template(self) -> str:
        return f"Clayton CA {self.display_name} Meeting {{}}{{}}"

    @property
    def compressed_title_prefix(self) -> str:
        """Prefix matched against compressed filenames + uploaded video titles."""
        return f"Clayton CA {self.display_name} Meeting"

    @property
    def video_title_datetime_format(self) -> str:
        return f"Clayton CA {self.display_name} Meeting %Y-%m-%d %I:%M %p"

    @property
    def video_title_date_format(self) -> str:
        return f"Clayton CA {self.display_name} Meeting %Y-%m-%d"

    @property
    def transcript_title_datetime_format(self) -> str:
        return f"{self.display_name} Meeting %Y-%m-%d %I:%M %p"

    @property
    def transcript_title_date_format(self) -> str:
        return f"{self.display_name} Meeting %Y-%m-%d"

    # --- wiki + playlist ---
    @property
    def wiki_year_template(self) -> str:
        return f"List of {{}} {self.display_name} Meetings"

    @property
    def playlist_name_template(self) -> str:
        return f"{{}} {self.display_name} Meetings"


# The City Council key is the pre-existing abbreviation, kept so all current
# Redis keys and filenames are unchanged. New types follow the same shape.
CITY_COUNCIL = MeetingType(
    key="cc_mtg", display_name="City Council", title_match="City Council"
)
PLANNING_COMMISSION = MeetingType(
    key="pc_mtg",
    display_name="Planning Commission",
    title_match="Planning Commission",
)

# The remaining bodies the city posts to CivicClerk. Each ``title_match`` and
# ``category`` is the exact portal ``categoryName`` so the API scraper routes by
# an exact match. GHAD keeps its short common name (matching its ``eventName``)
# as the display name for readable files/wiki pages, so its ``category`` — the
# portal's full legal name — is given explicitly.
BUDGET_AND_AUDIT = MeetingType(
    key="bac_mtg",
    display_name="Budget and Audit Committee",
    title_match="Budget and Audit Committee",
)
CITY_SPONSORED_SPECIAL_EVENTS = MeetingType(
    key="csse_mtg",
    display_name="City Sponsored Special Events Committee",
    title_match="City Sponsored Special Events Committee",
)
FINANCIAL_SUSTAINABILITY = MeetingType(
    key="fsc_mtg",
    display_name="Financial Sustainability Committee",
    title_match="Financial Sustainability Committee",
)
TRAILS_AND_LANDSCAPING = MeetingType(
    key="tlc_mtg",
    display_name="Trails and Landscaping Committee",
    title_match="Trails and Landscaping Committee",
)
GHAD = MeetingType(
    key="ghad_mtg",
    display_name="GHAD",
    title_match="Oakhurst Geological Hazard Abatement District",
    category="Oakhurst Geological Hazard Abatement District",
)
GENERAL = MeetingType(
    key="gen_mtg",
    display_name="General",
    title_match="General",
)

# Order matters for classification: the first matching type wins.
MEETING_TYPES: list[MeetingType] = [
    CITY_COUNCIL,
    PLANNING_COMMISSION,
    BUDGET_AND_AUDIT,
    CITY_SPONSORED_SPECIAL_EVENTS,
    FINANCIAL_SUSTAINABILITY,
    TRAILS_AND_LANDSCAPING,
    GHAD,
    GENERAL,
]


def classify(title: str) -> MeetingType | None:
    """Return the meeting type whose ``title_match`` appears in ``title``, if any."""
    for meeting_type in MEETING_TYPES:
        if meeting_type.title_match in title:
            return meeting_type
    return None
