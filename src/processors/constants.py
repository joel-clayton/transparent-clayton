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

VIDEO_DATE_KEY: str = "Date"
PUBLIC_VIDEO_STATUS = "public"
VIDEO_CATEGORY_ID = 25
CC_MTG_FILE_TEMPLATE = "City Council Meeting {} - City of Clayton{}"
CC_MTG_FILE_TEMPLATE_COMPRESSED = "Clayton CA City Council Meeting {} - %03d{}"
CC_MTG_VIDEO_TITLE_DATETIME_FORMAT = "Clayton CA City Council Meeting %Y-%m-%d %I:%M %p"
CC_MTG_VIDEO_TITLE_DATE_FORMAT = "Clayton CA City Council Meeting %Y-%m-%d"
CC_MTG_TRANSCRIPT_TITLE_FORMAT = "City Council Meeting %Y-%m-%d %I:%M %p"
CC_MTG_TRANSCRIPT_TITLE_DATE_FORMAT = "City Council Meeting %Y-%m-%d"
EARLIEST = datetime(2026, 3, 1)
