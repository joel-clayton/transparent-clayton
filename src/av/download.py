import logging
import os
import re
import subprocess

import requests

PLAYER_URL = (
    "https://claytonca.granicus.com/player/clip/{clip_id}?view_id=1&redirect=true"
)
OUTFILE_NAME = "City Council Meeting {} - City of Clayton.mp4"
DATE_FORMAT = "%Y-%m-%d"
OUTFILE_LOCATION = "/Volumes/Gautam/Clayton/CC Meetings/Downloaded"
TEST_CLIP_ID = 161

logging.basicConfig(level="DEBUG")
logFormatter = logging.Formatter(
    fmt="%(filename)s :: %(asctime)s,:: %(name)s :: %(levelname)-8s :: %(message)s"
)
logger = logging.getLogger(__name__)


def get_m3u_url(clip_id):
    response = requests.get(PLAYER_URL.format(clip_id=clip_id))
    # if response.status_code == 200:
    #     print(response.text)
    pattern = r"(https://archive-stream.*?playlist\.m3u8)"
    matches = re.findall(pattern, response.text)
    base = os.path.dirname(matches[0])
    url = base + "/chunklist.m3u8"
    return url


def get_media_stream(stream_url, output_file):
    ffmpeg_command = ["ffmpeg", "-i", stream_url, "-codec", "copy", output_file]
    subprocess.run(ffmpeg_command)
