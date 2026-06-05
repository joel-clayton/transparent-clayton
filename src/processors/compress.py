"""
This script compresses mp4 video takes a diff of videos based on a date in
 the filename and compresses videos from the src folder if they're
 not present in the destination folder.

Usage:
$ pipenv run python compress.py -d /Volumes/Gautam/Clayton/CC Meetings/Downloaded/ -t "-c:v libx265 -vtag hvc1" -o /Volumes/Gautam/Clayton/CC Meetings/Compressed/ -p "City of Clayton"
"""

import subprocess
from typing import List

import ffmpeg

from src.constants import COMPRESSED_CC_MTG_KEY
from src.processors.constants import VIDEO_SEGMENT_TIME, VIDEO_LARGE_SEGMENT_TIME
from src.processors.process import Processor
from src.settings import DOWNLOADED_DIR, COMPRESSED_DIR
from src.types import JobType, SourceType
from src.util import get_file_size_in_mb

SRC_FILE_NAME_TEMPLATE = "City Council Meeting {} - City of Clayton.mp4"
DST_FILE_NAME_TEMPLATE = "Clayton CA City Council Meeting {} - %03d.mp4"


class Compressor(Processor):
    def __init__(self) -> None:
        self.input_job_type = JobType.DOWNLOAD
        self.job_type = JobType.COMPRESS
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = COMPRESSED_CC_MTG_KEY
        super().__init__()

    def gather_input_dates(self) -> List:
        return self.gather_dates(DOWNLOADED_DIR)

    def gather_output_dates(self) -> List:
        return self.gather_dates(COMPRESSED_DIR)

    def process_for_date(self, date: str) -> None:
        """
        Submits the ffmpeg compression command for an absolute path input file
         and specifies an absolute path for output
        :return: None
        """
        input_filepath = self.construct_filepath_for_date(
            date, job_type=self.input_job_type
        )
        output_filepath = self.construct_filepath_for_date(date)
        input_stream = ffmpeg.input(input_filepath)
        needs_segmentation = get_file_size_in_mb(input_filepath) >= 256
        output_stream = ffmpeg.output(
            input_stream,
            output_filepath,
            vcodec="libx265",
            crf=28,
            f="segment",
            segment_time=VIDEO_SEGMENT_TIME
            if needs_segmentation
            else VIDEO_LARGE_SEGMENT_TIME,
            reset_timestamps=0,
            vtag="hvc1",
        )
        cmd = ffmpeg.compile(output_stream)
        result = subprocess.run(cmd)
        self.logger.debug(result.stdout)  # Output of the command
        self.logger.debug(result.stderr)  # Error messages (if any)
        self.logger.debug(result.returncode)  # Exit code of the command
        if result.stderr:
            raise Exception(result.stderr)
        self.log_complete_for_date(date=date)
