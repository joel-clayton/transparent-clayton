import re
from typing import TypedDict

import httplib2
import random
import time

import redis
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.av.constants import STATUS, CONTENT_DETAILS, CC_MTG_REDIS_KEY
from src.av.youtube_auth import get_authenticated_service

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
VALID_PRIVACY_STATUSES = ('public', 'private', 'unlisted')

DATE_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"


class VideoOptions(TypedDict):  # todo use this
  file: str
  title: str
  description: str
  cateogry: str
  keywords: str
  privacy_status: str


def get_iso_8601_from_date_str(date_str):
  return date_str


def initialize_upload(youtube, options):
  tags = None
  if options['keywords']:
    tags = options['keywords'].split(',')

  body=dict(
    snippet=dict(
      title=options['title'],
      description=options['description'],
      tags=tags,
    ),
    recordingDetails=dict(
      recordingDate=options['recording_date'],  # YYYY-MM-DDThh:mm:ss.sssZ
    ),
  )

  # Call the API's videos.insert method to create and upload the video.
  insert_request = youtube.videos().insert(
    part=','.join(body.keys()),
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
    media_body=MediaFileUpload(options['file'], chunksize=-1, resumable=True)
  )

  return resumable_upload(insert_request)

# This method implements an exponential backoff strategy to resume a
# failed upload.
def resumable_upload(request):
  response = None
  error = None
  retry = 0
  video_id = None
  while response is None:
    try:
      print('Uploading file...')
      status, response = request.next_chunk()
      if response is not None:
        if 'id' in response:
          video_id = response['id']
          print('Video id "%s" was successfully uploaded.' % video_id)
        else:
          exit('The upload failed with an unexpected response: %s' % response)
    except HttpError as e:
      if e.resp.status in RETRIABLE_STATUS_CODES:
        error = 'A retriable HTTP error %d occurred:\n%s' % (e.resp.status,
                                                             e.content)
      else:
        raise
    except RETRIABLE_EXCEPTIONS as e:
      error = 'A retriable error occurred: %s' % e

    if error is not None:
      print(error)
      retry += 1
      if retry > MAX_RETRIES:
        exit('No longer attempting to retry.')

      max_sleep = 2 ** retry
      sleep_seconds = random.random() * max_sleep
      print('Sleeping %f seconds and then retrying...' % sleep_seconds)
      time.sleep(sleep_seconds)
    return video_id


def upload_to_youtube(filepath: str, title: str, description: str, keywords: str, category_id: int, privacy_status: str = 'public'):
  options = {
    'file': filepath,
    'title': title,
    'description': description,
    'keywords': keywords,
    'privacy_status': privacy_status,
    'category': category_id,
  }

  match = re.search(DATE_PATTERN, filepath)
  if match:
    recording_date_str = get_iso_8601_from_date_str(match.group(0))
    options['recording_date'] = recording_date_str + "T12:00:00.000Z"  # redis.get(f"{CC_MTG_REDIS_KEY}:{recording_date_str}")

  youtube = get_authenticated_service()

  try:
    video_id = initialize_upload(youtube, options)
    return video_id
  except HttpError as e:
    print('An HTTP error %d occurred:\n%s' % (e.resp.status, e.content))
