"""
This script compresses mp4 video takes a diff of videos based on a date in
 the filename and compresses videos from the src folder if they're
 not present in the destination folder.

Usage:
$ pipenv run python kompress.py -d /Volumes/Gautam/Clayton/CC\ Meetings/Downloaded/ -t "-c:v libx265 -vtag hvc1" -o /Volumes/Gautam/Clayton/CC\ Meetings/Compressed/ -p "City of Clayton"
"""
import argparse
import logging
import os
import re
import subprocess
from os import listdir, path

import ffmpeg

from celery_app import app

logging.basicConfig(level='DEBUG')
logFormatter = logging.Formatter(fmt='%(filename)s :: %(asctime)s,'
                                     ':: %(name)s :: %(levelname)-8s '
                                     ':: %(message)s')
logger = logging.getLogger(__name__)
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)
SRC_FILE_NAME_TEMPLATE = 'City Council Meeting {} - City of Clayton.mp4'
DST_FILE_NAME_TEMPLATE = 'Clayton CA City Council Meeting {} - %03d.mp4'

def validate_args(parsed) -> bool:
    if bool(parsed.file) == bool(parsed.dir):
        raise Exception('Please specify either --file or --dir as inputs.')

    if parsed.out_dir is None:
        logger.warning('No output file was chosen. Out file will have '
                       'the same name as the --file.')
    return True


def parse_args():
    """
    Use argparse to parse arguments to this command
    :return:
    """
    parser = argparse.ArgumentParser(
        prog='compress',
        description='Compresses mp4 video by file or directory with some options',
        epilog='Good luck.'
    )

    parser.add_argument('-f', '--file')
    parser.add_argument('-d', '--dir')
    parser.add_argument('-o', '--out_dir')
    parser.add_argument('-t', '--options')
    parser.add_argument('-p', '--pattern')
    parsed = parser.parse_args()
    validate_args(parsed)

    return parsed

class Kompressor:
    file = None
    directory = None
    out_dir = None
    pattern = None
    options = None
    missing = []

    def __init__(self, parsed_args) -> None:
        """
        Set class attributes based on argparse of command line input
        :param parsed_args:
        """
        self.file = parsed_args.file
        self.directory = parsed_args.dir
        self.out_dir = parsed_args.out_dir
        self.options = parsed_args.options if parsed_args.pattern else ("-c:v "
                                                                  "libx265 -vtag hvc1")
        self.pattern = parsed_args.pattern

    def orchestrate(self) -> None:
        """
        - Loop through dates from file names present in src directory and
        not in destination directory
        - Construct the input and output file paths
        - Compress each video one by one
        :return:
        """
        dates = self.get_most_recent_missing_dates()
        if not dates:
            logger.info('No files to be compressed, good day')
            return
        compressed = []
        for date in dates:
            in_file = os.path.join(
                self.directory,
                SRC_FILE_NAME_TEMPLATE.format(date))#.replace(" ", "\ ")
            out_file = os.path.join(
                self.out_dir,
                DST_FILE_NAME_TEMPLATE.format(date))#.replace(" ", "\ ")
            logger.debug("IN: {}\n"
                         "OUT: {}".format(in_file, out_file))
            self.kompress(in_file, out_file)
            compressed.append(out_file)
        logger.info('compressed: {}'.format(compressed))

    def kompress(self, in_path: str, out_file: str) -> None:
        """
        Submits the ffmpeg compression command for an absolute path input file
         and specifies an absolute path for output
        :return: None
        """
        input_stream = ffmpeg.input(in_path)
        output_stream = ffmpeg.output(input_stream, out_file, vcodec='libx265', f='segment', segment_time=3600, reset_timestamps=1)
        cmd = ffmpeg.compile(output_stream)
        cmd = cmd[:-1] + ['-vtag', 'hvc1'] + cmd[-1:]
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
        dst_dates = sorted(self.gather_dates(self.out_dir, use_pattern=False))

        self.missing = sorted(list(set(src_dates) - set(dst_dates)))

        if not num or not self.missing:
            return self.missing
        return self.missing[-num:]

    def gather_dates(self, dir_path: str, use_pattern: bool=True) -> list:
        """
        Get all dates from file names in a specified directory, optionally
         using a pattern to filter file names down to a particular set
        :param dir_path: absolute path to look up files and parse dates in
        :param use_pattern: whether to use the command line arg pattern to
        match file names
        :return: list of dates, possibly empty
        """
        pattern = ""
        if use_pattern:
            pattern = self.pattern
        files = [f for f in listdir(dir_path) if path.isfile(os.path.join(
            dir_path, f)) if pattern in f]
        dates = []
        for f in files:
            date = re.search(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', f)
            if date:
                dates.append(date.group())
        return sorted(dates)


@app.task
def compress_full_sized_downloads():
    args = parse_args()
    k = Kompressor(args)
    k.orchestrate()
