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
from src.constants import DOWNLOADED_CC_MTG_KEY, RIPPED_CC_MTG_KEY, \
    SCRAPED_CC_MTG_KEY, FILE_LIST_MISMATCH
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


class Ripper:
    file: str | None = None
    directory: str | None = None
    out_dir: str | None = None
    pattern: str | None = None
    options: str | None = None
    missing: list | None = []

    def __init__(
        self,
        output_dir: str,
        input_dir: str | None = None,
        input_file: str | None = None
    ) -> None:
        """
        Set class attributes based on argparse of command line input
        """
        if bool(input_file) == bool(input_dir):
            raise Exception("Please specify either --file or --dir as inputs.")

        if output_dir is None:
            logger.warning(
                "No output file was chosen. Out file will have "
                "the same name as the --file."
            )

        self.file = input_file
        self.directory = input_dir
        self.out_dir = output_dir

    def orchestrate(self) -> None:
        """
        - Loop through dates from file names present in src directory and
        not in destination directory
        - Construct the input and output file paths
        - Rip audio for each video one by one
        """
        dates = self.get_most_recent_missing_dates()
        dates_to_process = get_detail_from_redis(SCRAPED_CC_MTG_KEY)
        if not dates:
            logger.info("No files to be ripped, good day")
            return
        if not dates_to_process and dates != dates_to_process:
            logger.error(FILE_LIST_MISMATCH)
        ripped = []
        for date in dates:
            in_file = str(os.path.join(
                self.directory, SRC_FILE_NAME_TEMPLATE.format(date)
            ))
            out_file = str(os.path.join(
                self.out_dir, DST_FILE_NAME_TEMPLATE.format(date)
            ))
            logger.debug("IN: {}\nOUT: {}".format(in_file, out_file))
            self.rip(in_file, out_file)
            ripped.append(out_file)
            r.hset(RIPPED_CC_MTG_KEY, date, 1)
            r.hdel(DOWNLOADED_CC_MTG_KEY, date)

        logger.info("ripped: {}".format(ripped))

    def rip(self, in_path: str, out_file: str) -> None:
        """
        Submits the ffmpeg rip command for an absolute path input file
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

    def get_most_recent_missing_dates(self, num: int = 0) -> list:
        """
        Compare two sets of dates from file names and find the list of src
         directory dates not represented in the destination directory
        :param num: number of missing dates to return
        :return: list of dates, possibly empty
        """
        src_dates = sorted(self.gather_dates(self.directory))
        dst_dates = sorted(self.gather_dates(self.out_dir))

        self.missing = sorted(list(set(src_dates) - set(dst_dates)))

        if not num or not self.missing:
            return self.missing
        return self.missing[-num:]

    def gather_dates(self, dir_path: str) -> list:
        """
        Get all dates from file names in a specified directory, optionally
         using a pattern to filter file names down to a particular set
        :param dir_path: absolute path to look up files and parse dates in
        :param use_pattern: whether to use the command line arg pattern to
        match file names
        :return: list of dates, possibly empty
        """
        files = [
            f
            for f in listdir(dir_path)
            if path.isfile(os.path.join(dir_path, f))
        ]
        dates = []
        for f in files:
            date = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", f)
            if date:
                dates.append(date.group())
        return sorted(dates)
