"""
This script compresses mp4 video takes a diff of videos based on a date in
 the filename and compresses videos from the src folder if they're
 not present in the destination folder.

Usage:
$ pipenv run python compress.py -d /Volumes/Gautam/Clayton/CC Meetings/Downloaded/ -t "-c:v libx265 -vtag hvc1" -o /Volumes/Gautam/Clayton/CC Meetings/Compressed/ -p "City of Clayton"
"""

import logging
import subprocess

import ffmpeg

from src.processors.process import Processor

logging.basicConfig(level="DEBUG")
logFormatter = logging.Formatter(
    fmt="%(filename)s :: %(asctime)s,:: %(name)s :: %(levelname)-8s :: %(message)s"
)
logger = logging.getLogger(__name__)
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)
SRC_FILE_NAME_TEMPLATE = "City Council Meeting {} - City of Clayton.mp4"
DST_FILE_NAME_TEMPLATE = "Clayton CA City Council Meeting {} - %03d.mp4"


class Compressor(Processor):
    def process_for_date(self, input_filepath: str, outout_filepath: str) -> None:
        """
        Submits the ffmpeg compression command for an absolute path input file
         and specifies an absolute path for output
        :return: None
        """
        input_stream = ffmpeg.input(input_filepath)
        output_stream = ffmpeg.output(
            input_stream,
            outout_filepath,
            vcodec="libx265",
            f="segment",
            segment_time=3600,
            reset_timestamps=1,
        )
        cmd = ffmpeg.compile(output_stream)
        cmd = cmd[:-1] + ["-vtag", "hvc1"] + cmd[-1:]
        result = subprocess.run(cmd)
        logger.debug("the commandline is {}".format(result.args))
        logger.debug(result.stdout)  # Output of the command
        logger.debug(result.stderr)  # Error messages (if any)
        logger.debug(result.returncode)  # Exit code of the command
        if result.stderr:
            raise Exception(result.stderr)
