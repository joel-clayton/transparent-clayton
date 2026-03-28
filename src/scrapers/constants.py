from pathlib import Path

SOURCE_URL = "https://claytonca.gov/government/city-council/"
DOWNLOADED_PATH = Path("/Volumes/Gautam/Clayton/CC Meetings/Downloaded")
VIDEO_FILE_NAME_TEMPLATE = (
    "City Council Meeting {} - City of Clayton.mp4"  # e.g. 2024-10-02
)
VIDEO_FILE_NAME_REGEX = (
    "^City Council Meeting [0-9]{4}-[0-9]{2}-[0-9]{2} - City of Clayton\\.mp4$"
)
VIDEO_DATE_REGEX = "[0-9]{4}-[0-9]{2}-[0-9]{2}"
DATETIME_INPUT_FORMAT = "%b %d, %Y-%H:%M %p"
DATETIME_OUTPUT_FORMAT = "%Y-%m-%d %H:%M"
DATE_OUTPUT_FORMAT = "%Y-%m-%d"
CLIP_ARG_REGEX = "clip_id=[0-9]*"
CLIP_ID_REGEX = "[0-9]+"

COMPRESSION_OPTIONS = "-c:v libx265 -vtag hvc1"
COMPRESSION_PATTERN = "City of Clayton"

DEFAULT_ONE_WEEK_SECONDS_EXPIRATION = 60 * 60 * 24 * 7
