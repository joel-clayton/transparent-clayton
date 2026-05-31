from enum import Enum
from typing import TypedDict, List

from src.constants import (
    AUDIO_TRANSCRIBED_CC_MTG_KEY,
    CC_MTG_FILE_STUB,
    COMPRESSED_CC_MTG_KEY,
    DOWNLOADED_CC_MTG_KEY,
    EXTRACTED_CC_MTG_KEY,
    SCRAPED_CC_MTG_KEY,
    TRANSCRIPT_UPLOADED_CC_MTG_KEY,
    VIDEO_UPLOADED_CC_MTG_KEY,
    CC_MTG_PARENT_FOLDER_ID,
)
from src.processors.constants import (
    CC_MTG_FILE_TEMPLATE,
    CC_MTG_FILE_TEMPLATE_COMPRESSED,
)
from src.settings import (
    COMPRESSED_DIR,
    DOWNLOADED_DIR,
    EXTRACTED_AUDIO_DIR,
    TRANSCRIBED_DIR,
)


class SourceType(Enum):
    CITY_COUNCIL_MEETING = 1
    BUDGET_AND_AUDIT_MEETING = 2


type_stubs = {SourceType.CITY_COUNCIL_MEETING: CC_MTG_FILE_STUB}


class JobType(Enum):
    SCRAPE = 1
    DOWNLOAD = 2
    COMPRESS = 3
    EXTRACT_AUDIO = 4
    TRANSCRIBE_AUDIO = 5
    UPLOAD_TRANSCRIPT = 6
    UPLOAD_VIDEO = 7
    UPDATE_WIKI = 8


job_paths = {
    JobType.DOWNLOAD: DOWNLOADED_DIR,
    JobType.COMPRESS: COMPRESSED_DIR,
    JobType.EXTRACT_AUDIO: EXTRACTED_AUDIO_DIR,
    JobType.TRANSCRIBE_AUDIO: TRANSCRIBED_DIR,
}


source_file_templates = {
    SourceType.CITY_COUNCIL_MEETING: CC_MTG_FILE_TEMPLATE,
}

source_job_file_templates = {
    SourceType.CITY_COUNCIL_MEETING: {
        JobType.DOWNLOAD: CC_MTG_FILE_TEMPLATE,
        JobType.COMPRESS: CC_MTG_FILE_TEMPLATE_COMPRESSED,
        JobType.EXTRACT_AUDIO: CC_MTG_FILE_TEMPLATE,
        JobType.TRANSCRIBE_AUDIO: CC_MTG_FILE_TEMPLATE,
    }
}

job_file_formats = {
    JobType.DOWNLOAD: ".mp4",
    JobType.COMPRESS: ".mp4",
    JobType.EXTRACT_AUDIO: ".m4a",
    JobType.TRANSCRIBE_AUDIO: ".txt",
}

job_redis_keys = {
    JobType.SCRAPE: SCRAPED_CC_MTG_KEY,
    JobType.DOWNLOAD: DOWNLOADED_CC_MTG_KEY,
    JobType.COMPRESS: COMPRESSED_CC_MTG_KEY,
    JobType.EXTRACT_AUDIO: EXTRACTED_CC_MTG_KEY,
    JobType.TRANSCRIBE_AUDIO: AUDIO_TRANSCRIBED_CC_MTG_KEY,
    JobType.UPLOAD_TRANSCRIPT: TRANSCRIPT_UPLOADED_CC_MTG_KEY,
    JobType.UPLOAD_VIDEO: VIDEO_UPLOADED_CC_MTG_KEY,
}

job_drive_parent_id = {JobType.UPLOAD_TRANSCRIPT: CC_MTG_PARENT_FOLDER_ID}


class Meeting(TypedDict):
    key: str
    duration: str
    agenda: str
    minutes_and_supplemental_materials: dict | None
    video: str
    agenda_packet: str
    clip_id: str
    source_type: str


class WikiMeeting(Meeting):
    video_backup_links: List[str]
    transcript_link: str
