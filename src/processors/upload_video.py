import json
import os
import random
import re
import time
from datetime import datetime
from os import listdir, path
from typing import Any, List, TypedDict
from urllib.request import Request

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

from celery_app import r
from src.constants import (
    DETAIL_CC_MTG_KEY,
    VIDEO_UPLOADED_CC_MTG_KEY,
    VIDEO_PLAYLIST_CC_MTG_KEY_TEMPLATE,
    VIDEO_PLAYLIST_NAME_TEMPLATE,
    DATE_PATTERN,
    DATETIME_FORMAT,
    DATE_FORMAT,
    VIDEO_LINK_TEMPLATE,
    VIDEO_LINK_CC_MTG_KEY_TEMPLATE,
)
from src.processors.process import Processor
from src.settings import COMPRESSED_DIR
from src.types import JobType, SourceType, type_stubs, Meeting
from src.processors.constants import (
    PUBLIC_VIDEO_STATUS,
    VIDEO_CATEGORY_ID,
    CC_MTG_VIDEO_TITLE_DATETIME_FORMAT,
    CC_MTG_VIDEO_TITLE_DATE_FORMAT,
    MIN_COMPRESSED_VIDEO_MB,
)
from src.util import (
    get_year_string_from_string,
    get_date_string_from_string,
    get_date_or_datetime_string_from_string,
    get_datetime_string_from_string,
    send_to_discord_bots,
    get_part_num_from_string,
)

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1

# Maximum number of times to retry before giving up.
MAX_RETRIES = 10

# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError)

# Always retry when an apiclient.errors.HttpError with one of these status
# codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")

TITLE_FORMAT = "Clayton CA City Council Meeting %Y %m %d"
COMPRESSED_TITLE_PATTERN = "Clayton CA City Council Meeting"
FILEPATH_TEMPLATE = "Clayton CA City Council Meeting {} - {}{}"

DESCRIPTION = "Unedited video from claytonca.gov"
KEYWORDS = "news, politics"
CLIENT_SECRETS_FILE = "/Users/gautam/dev/client_secret.json"

# This OAuth 2.0 access scope allows an application to upload files to the
# authenticated user's YouTube channel, but doesn't allow other types of access.\
SCOPES = ["https://www.googleapis.com/auth/youtube"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

RESULT_COUNT = 30


class PlaylistInfo(TypedDict):
    playlist_id: str
    playlist_year: str


class VideoUploader(Processor):
    source_dir: str
    job_type: JobType
    source_type: SourceType
    redis_key: str

    def __init__(self) -> None:
        self.job_type = JobType.UPLOAD_VIDEO
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = VIDEO_UPLOADED_CC_MTG_KEY
        self.service = self.authenticate()
        self.playlists: List[PlaylistInfo | None] = []
        self.videos: dict = {}
        super().__init__()

    def authenticate(self):  # type: ignore
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        credentials = flow.run_local_server(port=8080)
        return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

    def get_playlists(self) -> None:
        playlists_request = self.service.playlists().list(
            mine=True, part="snippet,contentDetails"
        )
        response = playlists_request.execute()

        playlists = dict(
            [(d["id"], d["snippet"]["title"]) for d in response.get("items")]
        )
        for playlist_id, title in playlists.items():
            year_str = get_year_string_from_string(title)
            if year_str:
                playlist_info: PlaylistInfo = {playlist_id: year_str}  # type: ignore
                self.playlists.append(playlist_info)
                redis_key = VIDEO_PLAYLIST_CC_MTG_KEY_TEMPLATE.format(year_str)
                r.set(redis_key, playlist_id)

    def create_playlist_for_year(self, year_str: str) -> str:
        request = self.service.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": VIDEO_PLAYLIST_NAME_TEMPLATE.format(year_str),
                },
                "status": {"privacyStatus": "public"},
            },
        )
        response = request.execute()
        return response["id"]

    def get_playlist_for_year(self, year_str: str) -> str:
        if not self.playlists:
            self.get_playlists()
        playlist_id = r.get(VIDEO_PLAYLIST_CC_MTG_KEY_TEMPLATE.format(year_str))
        if not playlist_id:
            return self.create_playlist_for_year(year_str)
        return playlist_id.decode("utf-8")

    def add_video_to_playlist(self, playlist_id: str, video_id: str) -> str:
        self.logger.info(f"playlist_id: {playlist_id}, video_id: {video_id}")
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            },
        }

        playlist_items_insert_request = self.service.playlistItems().insert(
            part="snippet", body=body
        )
        try:
            response = playlist_items_insert_request.execute()
            return response["id"]
        except Exception as e:
            self.logger.error(
                e, extra={"playlist_id": playlist_id, "video_id": video_id}
            )
        return ""

    def initialize_upload(self, options: dict[str, Any]) -> str:
        tags = None
        if options["keywords"]:
            tags = options["keywords"].split(",")

        body = dict(
            snippet=dict(
                title=options["title"],
                description=options["description"],
                tags=tags,
            ),
        )
        if options.get("recording_date"):
            body.update(
                {
                    "recordingDetails": {
                        "recordingDate": options[
                            "recording_date"
                        ],  # YYYY-MM-DDThh:mm:ss.sssZ
                    },
                }
            )

        # Call the API's videos.insert method to create and upload the video.
        insert_request = self.service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            # The chunksize parameter specifies the size of each chunk of data, in
            # bytes, that will be uploaded at a time. Set a higher value for
            # reliable connections as fewer chunks lead to faster uploads. Set a lower
            # value for better recovery on less reliable connections.
            #
            # Setting 'chunksize' equal to -1 in the code below means that the entire
            # file will be uploaded in a single HTTP request. (If the upload fails,
            # it will still be retried where it left off.) This is usually a best
            # practice, but if you're using Python older than 2.6 or if you're
            # running on App Engine, you should set the chunksize to something like
            # 1024 * 1024 (1 megabyte).
            media_body=MediaFileUpload(options["file"], chunksize=-1, resumable=True),
        )

        return self.resumable_upload(insert_request)

    def resumable_upload(self, request: Request) -> str:
        response = None
        error = None
        retry = 0
        video_id = ""
        while response is None:
            try:
                self.logger.info("Uploading file...")
                status, response = request.next_chunk()  # type: ignore
                if response is not None:
                    if "id" in response:
                        video_id = response["id"]
                        self.logger.info(
                            'Video id "%s" was successfully uploaded.' % video_id
                        )
                    else:
                        exit(
                            "The upload failed with an unexpected response: %s"
                            % response
                        )
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = "A retriable HTTP error %d occurred:\n%s" % (
                        e.resp.status,
                        e.content,
                    )
                else:
                    raise
            except RETRIABLE_EXCEPTIONS as e:
                error = "A retriable error occurred: %s" % e

            if error is not None:
                self.logger.error(error)
                retry += 1
                if retry > MAX_RETRIES:
                    exit("No longer attempting to retry.")

                max_sleep = 2**retry
                sleep_seconds = random.random() * max_sleep
                self.logger.debug(
                    "Sleeping %f seconds and then retrying..." % sleep_seconds
                )
                time.sleep(sleep_seconds)
        return video_id

    def gather_dates(self, dir_path: str) -> List:
        file_name_stub = type_stubs.get(self.source_type, "")
        files = [
            f
            for f in listdir(dir_path)
            if path.isfile(os.path.join(dir_path, f))
            if file_name_stub in f
        ]
        dates = []
        for f in files:
            date_match = re.search(COMPRESSED_TITLE_PATTERN, f)
            if date_match:
                absolute_path = os.path.join(dir_path, f)
                dates.append(absolute_path)
        return sorted(dates)

    def gather_input_dates(self) -> List:
        return sorted(self.gather_dates(COMPRESSED_DIR), reverse=True)[:RESULT_COUNT]

    def get_my_uploads_list(self) -> List | None:
        # Retrieve the contentDetails part of the channel resource for the
        # authenticated user's channel.
        channels_response = (
            self.service.channels()
            .list(
                mine=True,
                part="contentDetails",
            )
            .execute()
        )

        for channel in channels_response["items"]:
            # From the API response, extract the playlist ID that identifies the list
            # of videos uploaded to the authenticated user's channel.
            return channel["contentDetails"]["relatedPlaylists"]["uploads"]

        return None

    def update_video_links_in_redis(self, video_ids: dict[str, str]) -> None:
        for title, video_id in video_ids.items():
            meeting_key = get_date_or_datetime_string_from_string(title)
            part_num = get_part_num_from_string(title)
            redis_key = VIDEO_LINK_CC_MTG_KEY_TEMPLATE.format(
                meeting_key=meeting_key,
                part_num=part_num,
            )

            r.set(redis_key, VIDEO_LINK_TEMPLATE.format(video_id))

    def get_recent_video_titles(
        self, uploads_playlist_id: List, result_count: int = RESULT_COUNT
    ) -> List:
        # Retrieve the list of videos uploaded to the authenticated user's channel.
        playlistitems_list_request = self.service.playlistItems().list(
            playlistId=uploads_playlist_id,
            part="snippet",
            maxResults=result_count,
        )
        self.logger.debug("Videos in list %s" % uploads_playlist_id)

        playlistitems_list_response = playlistitems_list_request.execute()

        video_titles = []
        video_ids = {}
        for playlist_item in playlistitems_list_response["items"]:
            title = playlist_item["snippet"]["title"]
            video_titles.append(title)
            video_id = playlist_item["snippet"]["resourceId"]["videoId"]
            video_ids[title] = video_id
        self.update_video_links_in_redis(video_ids)
        return video_titles

    def get_output_title_from_input(self, filepath: str) -> str:
        absolute_path_parts = os.path.split(filepath)
        filename = absolute_path_parts[-1]
        # derive output title format from input file
        # todo handle different date formats in different time periods
        try:
            datetime_str = get_datetime_string_from_string(filename)
            dt = datetime.strptime(datetime_str, DATETIME_FORMAT)
            output_title = dt.strftime(CC_MTG_VIDEO_TITLE_DATETIME_FORMAT)
        except Exception:
            self.logger.info(f"Failed to parse date from {filepath}")
            date_str = get_date_string_from_string(filename)
            dt = datetime.strptime(date_str, DATE_FORMAT)
            output_title = dt.strftime(CC_MTG_VIDEO_TITLE_DATE_FORMAT)

        # convert part number to title part number if necessary
        part_match = re.search(r"[0-9]{3}\.", filename)
        if part_match:
            part_num = int(part_match.group(0).replace(".", ""))
            if part_num > 0:
                part_str = f" part {part_num + 1}"
            else:
                part_str = ""
        return output_title + part_str

    def gather_output_dates(self) -> List:
        uploads_playlist_id = self.get_my_uploads_list()
        titles = []
        if uploads_playlist_id:
            uploaded_titles = self.get_recent_video_titles(uploads_playlist_id)
            for uploaded in uploaded_titles:
                title_match = re.search(COMPRESSED_TITLE_PATTERN, uploaded)
                if title_match:
                    titles.append(uploaded)
                    date_match = re.search(DATE_PATTERN, title_match.group(0))
                    if date_match:
                        date_str = date_match.group(0)
                        video_details = self.videos.get(date_str, {})
                        video_details[date_str]
        return sorted(titles)

    def get_most_recent_missing_dates(self) -> List[str]:
        input_dates = sorted(self.gather_input_dates())
        output_dates = sorted(self.gather_output_dates())
        inputs = dict(
            [
                (
                    f"{get_date_or_datetime_string_from_string(input_date)}_{get_part_num_from_string(input_date)}",
                    input_date,
                )
                for input_date in input_dates
            ]
        )
        outputs = dict(
            [
                (
                    f"{get_date_or_datetime_string_from_string(output_date)}_{get_part_num_from_string(output_date)}",
                    output_date,
                )
                for output_date in output_dates
            ]
        )
        missing_output_dates = sorted(list(set(inputs.keys()) - set(outputs.keys())))
        return [inputs[date] for date in inputs if date in missing_output_dates]

    def get_publish_datetime(self, date: str) -> str:
        """
        Get this from the redis data for a given date
        """
        cc_meeting_detail_str = r.hget(DETAIL_CC_MTG_KEY, date)
        if not cc_meeting_detail_str:
            self.logger.warning(
                f"Date set for video upload is missing details in Redis -- {date}"
            )
            return ""
        cc_meeting_detail: Meeting = json.loads(cc_meeting_detail_str)
        if not cc_meeting_detail or not isinstance(cc_meeting_detail, dict):
            self.logger.warning(
                f"Malformed City Council Meeting details in Redis -- {date}"
            )
            return ""
        return cc_meeting_detail.get("key", "")

    def get_file_size(self, filepath: str) -> float:
        file_size_bytes = os.path.getsize(filepath)
        return file_size_bytes / (1024 * 1024)

    def process_for_date(self, date: str) -> None:
        if not self.service:
            self.authenticate()
        if not self.playlists:
            self.get_playlists()
        if self.get_file_size(date) < MIN_COMPRESSED_VIDEO_MB:
            self.logger.info(f"Skipping {date}, video file size is below minimum")
            return None
        date_str = get_date_string_from_string(date)
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        options = {
            "file": date,
            "title": self.get_output_title_from_input(date),
            "description": DESCRIPTION,
            "keywords": KEYWORDS,
            "privacy_status": PUBLIC_VIDEO_STATUS,
            "category": VIDEO_CATEGORY_ID,
        }
        publish_date = self.get_publish_datetime(date)
        if publish_date:
            options.update(recording_date=publish_date)

        try:
            self.logger.info(f"Uploading video for {dt}")
            video_id = self.initialize_upload(options)
            year_str = datetime.strftime(dt, "%Y")
            playlist_id = self.get_playlist_for_year(year_str)
            self.add_video_to_playlist(playlist_id, video_id)
            if not video_id:
                raise Exception("No Video ID returned")
        except HttpError as e:
            self.logger.error(
                "An HTTP error %d occurred:\n%s" % (e.resp.status, e.content)
            )

        self.log_complete_for_date(date=date)
        send_to_discord_bots(f"{self.job_type.name} completed for {date}")
        return None
