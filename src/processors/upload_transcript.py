import logging
import os
import re
from datetime import datetime
from typing import List

import googleapiclient.discovery
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload

from src.constants import TRANSCRIPT_UPLOADED_CC_MTG_KEY
from src.processors.constants import CC_MTG_FILE_TEMPLATE, EARLIEST
from src.processors.process import Processor
from src.processors.upload_video import DATE_PATTERN
from src.settings import TRANSCRIBED_DIR
from src.types import JobType, SourceType

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.metadata",
    "https://www.googleapis.com/auth/drive.file",
]
DESKTOP_APP_CLIENT_SECRET = "/Users/gautam/dev/client_secret_721413148557-p0c4gqeha85bo7astbjc9c29hp3a30b6.apps.googleusercontent.com.json"
TRANSCRIPTS_PARENT_ID = "1MR8u-c-eFDXSPef1tHFFknivWjJ5tp79"
TRANSCRIPT_FILE_TEMPLATE = "City Council Meeting {}"
SHARE_LIST = ["grahamjordan2596@gmail.com"]
logger = logging.getLogger(__name__)


class TranscriptUploader(Processor):
    def __init__(self) -> None:
        self.job_type = JobType.UPLOAD_TRANSCRIPT
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = TRANSCRIPT_UPLOADED_CC_MTG_KEY
        self.service = self.authenticate()

    def share_file(self, file_id: str, email: str) -> dict:
        # Define the permission properties
        permission_body = {
            "type": "user",
            "role": "writer",  # Options: 'reader', 'commenter', 'writer', 'fileOrganizer', 'organizer'
            "emailAddress": email,
        }

        # Execute the permission creation
        return (
            self.service.permissions()
            .create(
                fileId=file_id,
                body=permission_body,
                fields="id",
                sendNotificationEmail=True,  # Automatically sends an email notification
            )
            .execute()
        )

    def find_folder_id(self, folder_name: str) -> str | None:
        """Search for a folder by name and return its ID."""
        # Define the search query
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

        # Execute the list request
        results = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
                supportsAllDrives=True,
            )
            .execute()
        )

        folders = results.get("files", [])

        if not folders:
            print(f"No folder found with name: {folder_name}")
            return None

        # Return the ID of the first match
        return folders[0]["id"]

    def create_file(self, parent_id: str, date: str) -> str:  # type: ignore
        source_filename = CC_MTG_FILE_TEMPLATE.format(date, ".txt")
        destination_filename = TRANSCRIPT_FILE_TEMPLATE.format(date)
        file_metadata = {
            "name": destination_filename,
            "parents": [parent_id],
            "mimeType": "application/vnd.google-apps.document",
        }
        source_filepath = str(os.path.join(TRANSCRIBED_DIR, source_filename))
        media = MediaFileUpload(
            source_filepath,
            mimetype="text/plain",
            resumable=True,
        )

        # Execute the creation
        file = (
            self.service.files()
            .create(
                body=file_metadata,
                media_body=media,
                supportsAllDrives=True,
            )
            .execute()
        )
        return file["id"]

    def list_files_in_folder(self, folder_id: str) -> List:
        """Lists files in a specific Google Drive folder."""
        files = []
        page_token = None

        # Query: Specify the parent folder and exclude trashed files
        query = f"'{folder_id}' in parents and trashed = false"

        while True:
            # Call the Drive v3 API
            # 'fields' limits the returned data to improve performance
            results = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType)",
                    pageToken=page_token,
                )
                .execute()
            )

            files.extend(results.get("files", []))
            page_token = results.get("nextPageToken")

            if not page_token:
                break

        names = [d.get("name") for d in files]
        dates = []
        for name in names:
            date_match = re.search(DATE_PATTERN, name)
            if date_match:
                dates.append(date_match.group(0))
        return dates

    def create_folder(self, name: str) -> str:
        folder_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [TRANSCRIPTS_PARENT_ID],
        }
        folder = (
            self.service.files().create(body=folder_metadata, fields="id").execute()
        )
        return folder["id"]

    def authenticate(self):  # type: ignore
        flow = InstalledAppFlow.from_client_secrets_file(
            DESKTOP_APP_CLIENT_SECRET, SCOPES
        )
        credentials = flow.run_local_server(port=0)
        return googleapiclient.discovery.build("drive", "v3", credentials=credentials)

    def get_year_from_date(self, date: str) -> str:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return dt.strftime("%Y")

    def gather_input_dates(self) -> List:
        return sorted(self.gather_dates(TRANSCRIBED_DIR), reverse=True)

    def gather_output_dates(self) -> List:
        """
        Gather current previous year's worth of output dates
        """
        current_year = datetime.now().strftime("%Y")
        previous_year = str(int(current_year) - 1)
        output_dates = []
        for year in (current_year, previous_year):
            folder_id = self.find_folder_id(year)
            if not folder_id:
                self.create_folder(year)
                continue
            output_dates.extend(self.list_files_in_folder(folder_id))
        return sorted(output_dates, reverse=True)

    def process_for_date(self, date: str) -> None:
        dt = datetime.strptime(date, "%Y-%m-%d")
        if dt <= EARLIEST:
            logger.info(f"Skipping {date} because it falls before {EARLIEST}")
            return None
        year_folder_name = dt.strftime("%Y")
        parent_id = self.find_folder_id(year_folder_name)
        if not parent_id:
            parent_id = self.create_folder(year_folder_name)
        file_id = self.create_file(parent_id, date)

        for email in SHARE_LIST:
            result = self.share_file(file_id, email)
            print(f"Shared with {email}, Permission ID: {result.get('id')}")

        print(f"File ID: {file_id}")
