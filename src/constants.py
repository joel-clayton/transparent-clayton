DETAIL_KEY = "detail"
SCRAPED_KEY = "scraped"
DOWNLOADED_KEY = "downloaded"
COMPRESSED_KEY = "compressed"
RIPPED_KEY = "ripped"
UPLOADED_KEY = "uploaded"

CC_MTG_KEY = "cc_mtg"

DETAIL_CC_MTG_KEY = f"{DETAIL_KEY}.{CC_MTG_KEY}"
SCRAPED_CC_MTG_KEY = f"{SCRAPED_KEY}.{CC_MTG_KEY}"
DOWNLOADED_CC_MTG_KEY = f"{DOWNLOADED_KEY}.{CC_MTG_KEY}"
COMPRESSED_CC_MTG_KEY = f"{COMPRESSED_KEY}.{CC_MTG_KEY}"
RIPPED_CC_MTG_KEY = f"{RIPPED_KEY}.{CC_MTG_KEY}"
UPLOADED_CC_MTG_KEY = f"{UPLOADED_KEY}.{CC_MTG_KEY}"

"""
Redis key schema

[x] scrapers.cc_meetings task writes:
- detail.cc_mtg: {<date>: json(cc_mtg info)}
- scraped.cc_mtg: <json(sorted list of unprocessed cc_mtg dates)>

[x] av.download task reads:
- scraped.cc_mtg
    - pulls out list of unprocessed cc_mtg dates
writes:
- downloaded.cc_mtg: {<date>: 1}

[x] av.compressed task reads:
- scraped.cc_mtg
    - pulls out list of unprocessed cc_mtg dates
- downloaded.cc_mtg: <date> (compares to file paths diff)
writes:
- compressed.cc_mtg: {<date>: 1}
deletes:
- downloaded.cc_mtg: <date>

LAST STEP
deletes:
- scraped.cc_mtg
"""

FILE_LIST_MISMATCH = "Mismatch between Redis and file paths for downloaded videos to be compressed"

DEFAULT_ONE_WEEK_SECONDS_EXPIRATION = 60 * 60 * 24 * 7

# File path locations
DOWNLOADED_DIR = "/Users/gautam/Movies/4K Video Downloader+/Downloaded/" #"/Volumes/Gautam/Clayton/CC Meetings/Downloaded/"
COMPRESSED_DIR = "/Volumes/Gautam/Clayton/CC Meetings/Compressed/"
AUDIO_DIR = "/Users/gautam/Movies/4K Video Downloader+/Audio/" #"/Volumes/Gautam/Clayton/CC Meetings/Audio/"
COMPRESSION_OPTIONS = "-c:v libx265 -vtag hvc1"
COMPRESSION_PATTERN = "City of Clayton"
