import json
import random
import re
import time
from datetime import datetime
from typing import Any, List
from urllib.request import Request

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

from celery_app import r
from src.constants import DETAIL_CC_MTG_KEY, VIDEO_UPLOADED_CC_MTG_KEY
from src.processors.process import Processor
from src.scrapers.cc_meetings import CityCouncilMeeting
from src.settings import COMPRESSED_DIR
from src.types import JobType, SourceType
from src.processors.constants import (
    PUBLIC_VIDEO_STATUS,
    VIDEO_CATEGORY_ID,
    VIDEO_DATE_KEY,
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

DATE_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
TITLE_FORMAT = "Clayton CA City Council Meeting %Y %m %d"
FILEPATH_FORMAT = "City Council Meeting %Y%m%d - City of Clayton.mp4"

DESCRIPTION = "Unedited video from claytonca.gov"
KEYWORDS = "news, politics"
CLIENT_SECRETS_FILE = "/Users/gautam/dev/client_secret.json"

# This OAuth 2.0 access scope allows an application to upload files to the
# authenticated user's YouTube channel, but doesn't allow other types of access.\
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

RESULT_COUNT = 10


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

    def authenticate(self):  # type: ignore
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        credentials = flow.run_local_server(port=8080)
        return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

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
            recordingDetails=dict(
                recordingDate=options["recording_date"],  # YYYY-MM-DDThh:mm:ss.sssZ
            ),
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
                print("Uploading file...")
                status, response = request.next_chunk()  # type: ignore
                if response is not None:
                    if "id" in response:
                        video_id = response["id"]
                        print('Video id "%s" was successfully uploaded.' % video_id)
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
                print(error)
                retry += 1
                if retry > MAX_RETRIES:
                    exit("No longer attempting to retry.")

                max_sleep = 2**retry
                sleep_seconds = random.random() * max_sleep
                print("Sleeping %f seconds and then retrying..." % sleep_seconds)
                time.sleep(sleep_seconds)
        return video_id

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

    def get_recent_video_titles(
        self, uploads_playlist_id: List, result_count: int = RESULT_COUNT
    ) -> List:
        # Retrieve the list of videos uploaded to the authenticated user's channel.
        playlistitems_list_request = self.service.playlistItems().list(
            playlistId=uploads_playlist_id,
            part="snippet",
            maxResults=result_count,
        )
        print("Videos in list %s" % uploads_playlist_id)

        playlistitems_list_response = playlistitems_list_request.execute()

        video_titles = []
        # Print information about each video.
        for playlist_item in playlistitems_list_response["items"]:
            title = playlist_item["snippet"]["title"]
            video_titles.append(title)

        return video_titles

    def gather_output_dates(self) -> List:
        uploads_playlist_id = self.get_my_uploads_list()
        titles = []
        if uploads_playlist_id:
            uploaded_titles = self.get_recent_video_titles(uploads_playlist_id)
            for uploaded in uploaded_titles:
                date_match = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", uploaded)
                if date_match:
                    titles.append(date_match.group(0))
        return titles

    def get_publish_datetime(self, date: str) -> str:
        """
        Get this from the redis data for a given date
        """
        cc_meeting_detail_str = r.hget(DETAIL_CC_MTG_KEY, date)
        if not cc_meeting_detail_str:
            raise Exception("Date set for video upload is missing details in Redis")
        cc_meeting_detail: CityCouncilMeeting = json.loads(cc_meeting_detail_str)
        if not cc_meeting_detail or isinstance(cc_meeting_detail, dict):
            raise Exception("Malformed City Council Meeting details in Redis")
        return cc_meeting_detail.get(VIDEO_DATE_KEY)

    def process_for_date(self, date: str) -> None:
        if not self.service:
            self.authenticate()

        dt = datetime.strptime(date, "%Y-%m-%d")
        publish_date = self.get_publish_datetime(date)
        options = {
            "file": dt.strftime(FILEPATH_FORMAT),
            "title": dt.strftime(TITLE_FORMAT),
            "description": DESCRIPTION,
            "keywords": KEYWORDS,
            "privacy_status": PUBLIC_VIDEO_STATUS,
            "category": VIDEO_CATEGORY_ID,
            "recording_date": publish_date,
        }

        try:
            video_id = self.initialize_upload(options)
            if not video_id:
                raise Exception("No Video ID returned")
        except HttpError as e:
            print("An HTTP error %d occurred:\n%s" % (e.resp.status, e.content))
        return None
