from datetime import datetime

SNIPPET = dict(
    defaultAudioLanguage="English",
)

STATUS = dict(
    publicStatsViewable=True,
    selfDeclaredMadeForKids=False,
    containsSyntheticMedia=False,
)

CONTENT_DETAILS = dict(
    licensedContent=False,
    caption="False",
)

VIDEO_DATE_KEY: str = "key"
PUBLIC_VIDEO_STATUS = "public"
VIDEO_CATEGORY_ID = 25
VIDEO_SEGMENT_TIME = 3600
VIDEO_LARGE_SEGMENT_TIME = 60 * 60 * 12
CC_MTG_FILE_TEMPLATE = "City Council Meeting {} - City of Clayton{}"
CC_MTG_FILE_TEMPLATE_YT_DLP = "City Council Meeting {} - City of Clayton.%(ext)s"
CC_MTG_FILE_TEMPLATE_COMPRESSED_SEGMENTED = (
    "Clayton CA City Council Meeting {} - %03d{}"
)
CC_MTG_FILE_TEMPLATE_COMPRESSED_NOT_SEGMENTED = "Clayton CA City Council Meeting {}{}"
CC_MTG_VIDEO_TITLE_DATETIME_FORMAT = "Clayton CA City Council Meeting %Y-%m-%d %I:%M %p"
CC_MTG_VIDEO_TITLE_DATE_FORMAT = "Clayton CA City Council Meeting %Y-%m-%d"
CC_MTG_TRANSCRIPT_TITLE_FORMAT = "City Council Meeting %Y-%m-%d %I:%M %p"
CC_MTG_TRANSCRIPT_TITLE_DATE_FORMAT = "City Council Meeting %Y-%m-%d"
EARLIEST = datetime(2026, 3, 1)

CC_MTG_WIKI_YEAR_NAME_TEMPLATE = "List of {} City Council Meetings"

# Combined, type-agnostic transparency page listing meetings the city posted
# with no published materials (kept generic so other meeting types share it).
WIKI_NO_MATERIALS_PAGE = "Meetings Without Published Materials"
WIKI_NO_MATERIALS_INTRO = (
    "The following meetings were posted by the City of Clayton with no published "
    "agenda, documents, or video at the time they were scraped."
)
WIKI_NO_MATERIALS_EMPTY = "No meetings without published materials have been recorded."
# Shown on a meeting's own year-page entry when its recording has no usable audio.
WIKI_NO_AUDIO_NOTE = (
    "\n''No transcript is available: the recording published by the City for this "
    "meeting contains no usable audio.''\n"
)
# Second section of the transparency page: meetings with video but no usable audio.
WIKI_NO_AUDIO_SECTION_TITLE = "== Meetings Without Usable Audio =="
WIKI_NO_AUDIO_INTRO = (
    "The following meetings were published by the City of Clayton with video but "
    "no usable audio, so no transcript could be produced."
)

FORMAL_DATE_PATTERN = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
FORMAL_DATETIME_PATTERN = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4} \d{1,2}:\d{2} (?:AM|PM)"
TIME_PATTERN = r"\d{1,2}[:_]\d{2} (?:AM|PM)"
NATURAL_DATE_FORMAT = "%B %d, %Y"
NATURAL_DATETIME_FORMAT = "%B %d, %Y %I:%M %p"
MIN_COMPRESSED_VIDEO_MB = 3

WIKI_MTG_SECTION_TITLE = """== {meeting_key} =="""
WIKI_MTG_TABLE_OPEN = """
{| class="wikitable" style="margin-right:auto; margin-left: 0px;"
|-
"""
WIKI_MTG_TABLE_DATA = "\n| {table_data}"
WIKI_MTG_TABLE_CLOSE = "\n|}\n"
# Shown in place of the asset table when the city cancelled the meeting.
WIKI_MTG_CANCELLED = "\n'''Cancelled'''\n"

WIKI_AI_SECTION_TITLE = "=== AI summary ==="
WIKI_AI_SECTION = """
<div class="mw-collapsible mw-collapsed" style="overflow:auto;">
The following summary is provided by smmry.com.
<div class="mw-collapsible-content">
{ai_summary_text}
</div>
</div>

See the [{transcript_link} full transcript here.]

"""
