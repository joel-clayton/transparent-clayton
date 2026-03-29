"""
This script compresses mp4 video takes a diff of videos based on a date in
 the filename and compresses videos from the src folder if they're
 not present in the destination folder.

Usage:
$ pipenv run python compress.py -d /Volumes/Gautam/Clayton/CC Meetings/Downloaded/ -t "-c:v libx265 -vtag hvc1" -o /Volumes/Gautam/Clayton/CC Meetings/Compressed/ -p "City of Clayton"
"""
import logging
import os
import re
import subprocess
from os import listdir, path

import ffmpeg

from celery_app import r
from src.constants import DOWNLOADED_CC_MTG_KEY, EXTRACTED_CC_MTG_KEY, \
    SCRAPED_CC_MTG_KEY, FILE_LIST_MISMATCH
from src.processors.process import Processor
from src.util import get_detail_from_redis

logging.basicConfig(level="DEBUG")
logFormatter = logging.Formatter(
    fmt="%(filename)s :: %(asctime)s,:: %(name)s :: %(levelname)-8s :: %(message)s"
)
logger = logging.getLogger(__name__)
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)
SRC_FILE_NAME_TEMPLATE = "City Council Meeting {} - City of Clayton.mp4"
DST_FILE_NAME_TEMPLATE = "City Council Meeting {} - City of Clayton.m4a"


class Extractor(Processor):

    def process_for_date(self, in_path: str, out_file: str) -> None:
        """
        Submits the ffmpeg extract command for an absolute path input file
         and specifies an absolute path for output
        :return: None
        """
        input_stream = ffmpeg.input(in_path)
        output_stream = ffmpeg.output(
            input_stream,
            out_file,
            vn='null',
            acodec='copy',
        )
        cmd = ffmpeg.compile(output_stream)
        result = subprocess.run(cmd)
        logger.debug("the commandline is {}".format(result.args))
        logger.debug(result.stdout)  # Output of the command
        logger.debug(result.stderr)  # Error messages (if any)
        logger.debug(result.returncode)  # Exit code of the command
        if result.stderr:
            raise Exception(result.stderr)
