DETAIL_KEY = "detail"
SCRAPED_KEY = "scraped"
DOWNLOADED_KEY = "downloaded"
COMPRESSED_KEY = "compressed"
EXTRACTED_KEY = "extracted"
AUDIO_TRANSCRIBED_KEY = "audio_transcribed"
VIDEO_UPLOADED_KEY = "video_uploaded"
TRANSCRIPT_UPLOADED_KEY = "transcript_uploaded"

CC_MTG_KEY = "cc_mtg"

DETAIL_CC_MTG_KEY = f"{DETAIL_KEY}.{CC_MTG_KEY}"
SCRAPED_CC_MTG_KEY = f"{SCRAPED_KEY}.{CC_MTG_KEY}"
DOWNLOADED_CC_MTG_KEY = f"{DOWNLOADED_KEY}.{CC_MTG_KEY}"
COMPRESSED_CC_MTG_KEY = f"{COMPRESSED_KEY}.{CC_MTG_KEY}"
EXTRACTED_CC_MTG_KEY = f"{EXTRACTED_KEY}.{CC_MTG_KEY}"
AUDIO_TRANSCRIBED_CC_MTG_KEY = f"{AUDIO_TRANSCRIBED_KEY}.{CC_MTG_KEY}"
VIDEO_UPLOADED_CC_MTG_KEY = f"{VIDEO_UPLOADED_KEY}.{CC_MTG_KEY}"
TRANSCRIPT_UPLOADED_CC_MTG_KEY = f"{TRANSCRIPT_UPLOADED_KEY}.{CC_MTG_KEY}"

VIDEO_PLAYLIST_CC_MTG_KEY_TEMPLATE = "video_playlist.cc_mtg.{}"
VIDEO_PLAYLIST_NAME_TEMPLATE = "{} City Council Meetings"
CC_MTG_FILE_FORMAT = "City Council Meeting %Y-%m-%d - City of Clayton{}"
CC_MTG_FILE_STUB = "City Council Meeting"

CC_MTG_PARENT_FOLDER_ID = "1MR8u-c-eFDXSPef1tHFFknivWjJ5tp79"

"""
Redis key schema

INPUT
[x] scrapers.cc_meetings task writes:
- detail.cc_mtg: {<date>: json(cc_mtg info)}
- scraped.cc_mtg: <json(sorted list of unprocessed cc_mtg dates)>

[x] av.download task reads:
- determines dates by file structure
writes:
- downloaded.cc_mtg: {<date>: 1}

PROCESSING
[x] av.compressed task reads:
- determines dates by file structure
writes:
- compressed.cc_mtg: {<date>: 1}

[] av.extract_audio task reads:
- determines dates by file structure
writes:
- extracted.cc_mtg: {<date>: 1}

[] av.transcriber task reads:
- determines dates by file structure
writes:
- transcribed.cc_mtg: {<date>: 1}

OUTPUT
[] av.docs_uploader task reads:
- determines dates by file structure and drive query
writes:
- transcript_uploaded.cc_mtg: {<date>: 1}

[] av.yt_uploader task reads:
- determines dates by file structure and yt query
writes:
- video_uploaded.cc_mtg: {<date>: 1}

LAST STEP
deletes:
- scraped.cc_mtg
"""

FILE_LIST_MISMATCH = (
    "Mismatch between Redis and file paths for downloaded videos to be compressed"
)
DATE_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
DATETIME_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}_[0-9]{2} [APM]{2}"
DATETIME_OUTPUT_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} [APM]{2}"
YEAR_PATTERN = r"\b\d{4}\b"
DATETIME_FORMAT = "%Y-%m-%d %I_%M %p"
DATE_FORMAT = "%Y-%m-%d"
DATETIME_TITLE_PATTERN = "%Y-%m-%d %I:%M %p"
