from datetime import datetime
from pathlib import Path

SOURCE_URL = "https://claytonca.gov/government/city-council/"
CIVIC_CLERK_URL = "https://claytonca.portal.civicclerk.com/"
DOWNLOADED_PATH = Path("/Volumes/Gautam/Clayton/CC Meetings/Downloaded")
RAW_SNAPSHOT_PATH = Path("/Volumes/Gautam/Clayton/CC Meetings/RawSnapshots")
VIDEO_FILE_NAME_TEMPLATE = (
    "City Council Meeting {} - City of Clayton.mp4"  # e.g. 2024-10-02
)
VIDEO_FILE_NAME_REGEX = (
    "^City Council Meeting [0-9]{4}-[0-9]{2}-[0-9]{2} - City of Clayton\\.mp4$"
)
VIDEO_DATE_REGEX = "[0-9]{4}-[0-9]{2}-[0-9]{2}"
DATETIME_INPUT_FORMAT = "%b %d, %Y-%I:%M %p"
DATE_INPUT_FORMAT = "%b %d, %Y"
DATE_OUTPUT_FORMAT = "%Y-%m-%d"
CLIP_ARG_REGEX = "clip_id=[0-9]*"
CLIP_ID_REGEX = "[0-9]+"

COMPRESSION_OPTIONS = "-c:v libx265 -vtag hvc1"
COMPRESSION_PATTERN = "City of Clayton"

DEFAULT_ONE_WEEK_SECONDS_EXPIRATION = 60 * 60 * 24 * 7
CIVIC_CLERK_START_DATE = datetime(2026, 5, 10)

# Fault-tolerance knobs for unattended (cron-driven) scraping.
PAGE_LOAD_TIMEOUT = 45  # seconds; caps a hung driver.get so cron can't stall
LISTING_WAIT_TIMEOUT = 30  # seconds to wait for the meeting listing to hydrate
# Body text longer than this means the page rendered, so a missing selector is a
# structure change (needs a human) rather than a transient load failure.
MIN_RENDERED_BODY_CHARS = 200
SCRAPE_RETRY_ATTEMPTS = 3
SCRAPE_RETRY_BASE_DELAY = 2.0  # seconds; first backoff, doubled each retry
SCRAPE_RETRY_MAX_DELAY = 30.0  # seconds; per-backoff ceiling
SCRAPE_RETRY_DEADLINE = 120.0  # seconds; total wall-clock ceiling across retries
SCRAPE_URL_HEAD_TIMEOUT = 10.0  # seconds; per-request timeout for HEAD-resolve checks
