import os
import re
import time
from datetime import datetime
from time import sleep
from typing import List

import googleapiclient.discovery
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload

from src.constants import (
    TRANSCRIPT_UPLOADED_CC_MTG_KEY,
    DATE_PATTERN,
    DATETIME_OUTPUT_PATTERN,
)
from src.processors.constants import (
    CC_MTG_FILE_TEMPLATE,
    EARLIEST,
    CC_MTG_TRANSCRIPT_TITLE_FORMAT,
)
from src.processors.process import Processor
from src.settings import TRANSCRIBED_DIR
from src.types import JobType, SourceType, job_drive_parent_id
from src.util import get_year_string_from_string

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


class TranscriptUploader(Processor):
    def __init__(self) -> None:
        self.job_type = JobType.UPLOAD_TRANSCRIPT
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = TRANSCRIPT_UPLOADED_CC_MTG_KEY
        self.service = self.authenticate()
        super().__init__()

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
        parent_id = job_drive_parent_id.get(self.job_type)
        # Define the search query
        query = f"name = '{folder_name}' and '{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

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
            self.logger.debug(f"No folder found with name: {folder_name}")
            return None

        # Return the ID of the first match
        return folders[0]["id"]

    def create_file(self, parent_id: str, date: str) -> str:  # type: ignore
        source_filename = CC_MTG_FILE_TEMPLATE.format(date, ".txt")
        dt = self.extract_datetime_object(date)
        if not dt:
            raise Exception(f"Could not extract datetime from {date}")
        destination_filename = dt.strftime(CC_MTG_TRANSCRIPT_TITLE_FORMAT)
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
        max_retries = 5
        try:
            for attempt in range(max_retries):
                file_id = None
                while not file_id:
                    file = (
                        self.service.files()
                        .create(
                            body=file_metadata,
                            media_body=media,
                            supportsAllDrives=True,
                        )
                        .execute()
                    )
                    file_id = file.get("id")

                for email in SHARE_LIST:
                    user_permission = {
                        "type": "user",
                        "role": "reader",
                        "emailAddress": email,
                    }

                    sleep(5)
                    permission = (
                        self.service.permissions()
                        .create(
                            fileId=file_id,
                            body=user_permission,
                            fields="id",
                        )
                        .execute()
                    )
                    self.logger.info(
                        f"{destination_filename} updated with perms {permission}"
                    )
                wait = 2**attempt
                time.sleep(wait)

                return file_id
        except Exception as e:
            raise Exception(f"Could not upload transcript for {dt}: {e}")

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
            datetime_match = re.search(DATETIME_OUTPUT_PATTERN, name)
            if datetime_match:
                dates.append(datetime_match.group(0).replace(":", "_"))
            else:
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
        return sorted(self.gather_dates(TRANSCRIBED_DIR))

    def gather_output_dates(self) -> List:
        """
        Gather current previous year's worth of output dates
        """
        input_dates = self.gather_input_dates()
        input_years = sorted(
            set([get_year_string_from_string(input_date) for input_date in input_dates])
        )
        folders = {}
        for year in input_years:
            folders[year] = self.find_folder_id(year)
        files_list = []
        for folder_name, folder_id in folders.items():
            if not folder_id:
                self.logger.warning(f"No folder ID found for {folder_name}")
                continue
            files_list += self.list_files_in_folder(folder_id)

        return sorted(files_list)

    def process_for_date(self, date: str) -> None:
        print(f"Attempting to upload transcript for {date}")
        dt = self.extract_datetime_object(date)
        if dt and dt <= EARLIEST:
            self.logger.debug(f"Skipping {date}, older than {EARLIEST}")
            return None
        elif not dt:
            raise Exception(f"Could not parse date from {date} for {self.job_type}")
        self.logger.debug(f"Uploading transcript for {date}")
        year_folder_name = dt.strftime("%Y")
        parent_id = self.find_folder_id(year_folder_name)
        if not parent_id:
            parent_id = self.create_folder(year_folder_name)
        file_id = self.create_file(parent_id, date)

        for email in SHARE_LIST:
            result = self.share_file(file_id, email)
            self.logger.debug(f"Shared with {email}, Permission ID: {result.get('id')}")

        self.logger.debug(f"File ID: {file_id}")
