import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# CivicClerk timestamps are labeled 'Z' but render as wall-clock local time, so
# "now" and the lookback watermark are compared as naive local datetimes.
CIVIC_CLERK_TZ = ZoneInfo("America/Los_Angeles")

SOURCE_URL = "https://claytonca.gov/government/city-council/"
CIVIC_CLERK_URL = "https://claytonca.portal.civicclerk.com/"
# The portal SPA is backed by a public, no-auth OData API. Querying it with a
# date range returns every event (all types, all assets); the SPA's own default
# view only requests upcoming events, which is why DOM scraping missed the past.
CIVIC_CLERK_API_URL = "https://claytonca.api.civicclerk.com/v1/"
CIVIC_CLERK_EVENTS_ENDPOINT = CIVIC_CLERK_API_URL + "Events"
# A published document's stable stream URL, by numeric fileId (from an event's
# publishedFiles[]). Unlike the old scraped blob URLs, this does not expire.
CIVIC_CLERK_FILE_STREAM_TEMPLATE = (
    CIVIC_CLERK_API_URL
    + "Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"
)
# Video CDN base. An event's mediaStreamPath ("CLAYTONCA/<guid>.mp4") maps to a
# playable MP4 at this base, lowercased.
CIVIC_CLERK_MEDIA_BASE = "https://cpmedia.azureedge.net/"
# Per-request timeout and a safety cap on pagination (the API pages ~15/response
# via @odata.nextLink); the cap bounds a runaway follow loop.
CIVIC_CLERK_API_TIMEOUT = 30.0  # seconds
CIVIC_CLERK_API_MAX_PAGES = 400
# Overridable storage root (shared with src.settings) so a dry run can point the
# whole pipeline at a throwaway directory off the external volume.
_STORAGE_ROOT = os.environ.get(
    "PIPELINE_STORAGE_ROOT", "/Volumes/Gautam/Clayton/CC Meetings"
)
DOWNLOADED_PATH = Path(_STORAGE_ROOT) / "Downloaded"
RAW_SNAPSHOT_PATH = Path(_STORAGE_ROOT) / "RawSnapshots"
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
