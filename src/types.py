from enum import Enum
from typing import TypedDict, List

from src.constants import (
    AUDIO_TRANSCRIBED_CC_MTG_KEY,
    COMPRESSED_CC_MTG_KEY,
    DOWNLOADED_CC_MTG_KEY,
    EXTRACTED_CC_MTG_KEY,
    SCRAPED_CC_MTG_KEY,
    TRANSCRIPT_UPLOADED_CC_MTG_KEY,
    VIDEO_UPLOADED_CC_MTG_KEY,
    CC_MTG_PARENT_FOLDER_ID,
)
from src.meeting_types import (
    BUDGET_AND_AUDIT,
    CITY_COUNCIL,
    CITY_SPONSORED_SPECIAL_EVENTS,
    FINANCIAL_SUSTAINABILITY,
    GENERAL,
    GHAD,
    PLANNING_COMMISSION,
    TRAILS_AND_LANDSCAPING,
    MeetingType,
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
    PLANNING_COMMISSION = 3
    CITY_SPONSORED_SPECIAL_EVENTS = 4
    FINANCIAL_SUSTAINABILITY = 5
    TRAILS_AND_LANDSCAPING = 6
    GHAD = 7
    GENERAL = 8


# Bridge the enum (used as a dict key across the pipeline) to the MeetingType
# config that is the single source of every per-type string. Every configured
# CivicClerk category the city posts is routed here; the pipeline iterates these
# and scrapes/classifies each type on its own pass.
MEETING_TYPE_BY_SOURCE: dict[SourceType, MeetingType] = {
    SourceType.CITY_COUNCIL_MEETING: CITY_COUNCIL,
    SourceType.PLANNING_COMMISSION: PLANNING_COMMISSION,
    SourceType.BUDGET_AND_AUDIT_MEETING: BUDGET_AND_AUDIT,
    SourceType.CITY_SPONSORED_SPECIAL_EVENTS: CITY_SPONSORED_SPECIAL_EVENTS,
    SourceType.FINANCIAL_SUSTAINABILITY: FINANCIAL_SUSTAINABILITY,
    SourceType.TRAILS_AND_LANDSCAPING: TRAILS_AND_LANDSCAPING,
    SourceType.GHAD: GHAD,
    SourceType.GENERAL: GENERAL,
}


type_stubs = {source: mt.file_stub for source, mt in MEETING_TYPE_BY_SOURCE.items()}


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
    source: mt.file_template for source, mt in MEETING_TYPE_BY_SOURCE.items()
}

source_job_file_templates = {
    source: {
        JobType.DOWNLOAD: mt.file_template,
        JobType.COMPRESS: mt.compressed_segmented_template,
        JobType.EXTRACT_AUDIO: mt.file_template,
        JobType.TRANSCRIBE_AUDIO: mt.file_template,
    }
    for source, mt in MEETING_TYPE_BY_SOURCE.items()
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
    cancelled: bool


class WikiMeeting(Meeting):
    video_backup_links: List[str]
    transcript_link: str
