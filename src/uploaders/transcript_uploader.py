from datetime import datetime

import googleapiclient.discovery
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload

from celery_app import r
from src.constants import TRANSCRIPT_UPLOADED_CC_MTG_KEY
from src.types import JobType, SourceType
from src.util import get_detail_from_redis

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.metadata",
    "https://www.googleapis.com/auth/drive.file",
]
DESKTOP_APP_CLIENT_SECRET = "/Users/gautam/dev/client_secret_721413148557-p0c4gqeha85bo7astbjc9c29hp3a30b6.apps.googleusercontent.com.json"


class TranscriptUploader:
    source_dir: str
    job_type: JobType
    source_type: SourceType
    redis_key: str

    def __init__(
        self,
        source_dir: str,
    ):
        self.source_dir = source_dir
        self.job_type = JobType.UPLOAD_TRANSCRIPT
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = TRANSCRIPT_UPLOADED_CC_MTG_KEY

    def share_file(self, authed_service, file_id, email):
        # Define the permission properties
        permission_body = {
            'type': 'user',
            'role': 'writer',  # Options: 'reader', 'commenter', 'writer', 'fileOrganizer', 'organizer'
            'emailAddress': email
        }

        # Execute the permission creation
        return authed_service.permissions().create(
            fileId=file_id,
            body=permission_body,
            fields='id',
            sendNotificationEmail=True  # Automatically sends an email notification
        ).execute()

    def find_folder_id(self, service, folder_name):
        """Search for a folder by name and return its ID."""
        # Define the search query
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

        # Execute the list request
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            supportsAllDrives=True,
        ).execute()

        folders = results.get('files', [])

        if not folders:
            print(f"No folder found with name: {folder_name}")
            return None

        # Return the ID of the first match
        return folders[0]['id']

    def create_file(self, service, parent_id):
        file_metadata = {
            'name': 'MyNewFile3.txt',
            'parents': [parent_id]
        }
        media = MediaFileUpload(
            '/Users/gautam/Downloads/transcript.txt',
            mimetype='text/plain',
            # resumable=True
        )

        # Execute the creation
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True,
        ).execute()
        return file.get('id')

    def authenticate(self):
        flow = InstalledAppFlow.from_client_secrets_file(
            DESKTOP_APP_CLIENT_SECRET, SCOPES
        )
        credentials = flow.run_local_server(port=0)
        return googleapiclient.discovery.build(
            'drive', 'v3', credentials=credentials)

    def get_year_from_date(self, date):
        dt = datetime.strptime(date, '%Y-%m-%d')
        return dt.strftime('%Y')

    def upload_for_date(self, date):
        """Shows basic usage of the Drive v3 API.
        Prints the names and ids of the first 10 files the user has access to.
        """
        service = self.authenticate()
        folder_name = self.get_year_from_date(date)
        parent_id = self.find_folder_id(service, folder_name)
        file_id = self.create_file(service, parent_id)

        emails = ['grahamjordan2596@gmail.com', 'charlesrhine1857@gmail.com']
        for email in emails:
            result = self.share_file(service, file_id, email)
            print(f'Shared with {email}, Permission ID: {result.get("id")}')

        print(f"File ID: {file_id}")

    def upload(self):
        dates_to_upload = get_detail_from_redis(self.redis_key)
        if not dates_to_upload:
            return
        for date in dates_to_upload:
            self.upload_for_date(date)
            r.hset(self.redis_key, date, 1)