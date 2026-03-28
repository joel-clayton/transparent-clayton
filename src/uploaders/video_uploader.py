import json
import random
import time
from datetime import datetime

import httplib2
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from celery_app import r
from src.constants import SCRAPED_CC_MTG_KEY, DETAIL_CC_MTG_KEY, VIDEO_UPLOADED_CC_MTG_KEY
from src.types import JobType, SourceType
from src.uploaders.constants import PUBLIC_VIDEO_STATUS, VIDEO_CATEGORY_ID, VIDEO_DATE_KEY
from src.uploaders.youtube_auth import get_authenticated_service
from src.util import get_detail_from_redis

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


class VideoUploader:
    source_dir: str
    job_type: JobType
    source_type: SourceType
    redis_key: str

    def __init__(
        self,
        source_dir: str,
    ):
        self.source_dir = source_dir
        self.job_type = JobType.UPLOAD_VIDEO
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = VIDEO_UPLOADED_CC_MTG_KEY

    def initialize_upload(self, youtube, options):
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
        insert_request = youtube.videos().insert(
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


    # This method implements an exponential backoff strategy to resume a
    # failed upload.
    def resumable_upload(self, request) -> str:
        response = None
        error = None
        retry = 0
        video_id = None
        while response is None:
            try:
                print("Uploading file...")
                status, response = request.next_chunk()
                if response is not None:
                    if "id" in response:
                        video_id = response["id"]
                        print('Video id "%s" was successfully uploaded.' % video_id)
                    else:
                        exit("The upload failed with an unexpected response: %s" % response)
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

    def upload_for_date(
        self,
        date: str,
        publish_date: str,
    ) -> None:
        dt = datetime.strptime(date, '%Y-%m-%d')

        options = {
            "file": dt.strftime(FILEPATH_FORMAT),
            "title": dt.strftime(TITLE_FORMAT),
            "description": DESCRIPTION,
            "keywords": KEYWORDS,
            "privacy_status": PUBLIC_VIDEO_STATUS,
            "category": VIDEO_CATEGORY_ID,
            "recording_date": publish_date,
        }

        youtube = get_authenticated_service()

        try:
            video_id = self.initialize_upload(youtube, options)
            return video_id
        except HttpError as e:
            print("An HTTP error %d occurred:\n%s" % (e.resp.status, e.content))

    def upload(self):
        dates_to_upload = get_detail_from_redis(self.redis_key)
        if not dates_to_upload:
            return
        for date in dates_to_upload:
            cc_meeting_detail = r.hget(DETAIL_CC_MTG_KEY, date)
            if not cc_meeting_detail:
                raise "Date set for video upload is missing details in Redis"
            cc_meeting_detail = json.loads(cc_meeting_detail)
            publish_ds = cc_meeting_detail.get(VIDEO_DATE_KEY)
            self.upload_for_date(date, publish_ds)
            r.hset(self.redis_key, date, 1)
