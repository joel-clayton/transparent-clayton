import logging
import os
import re
from datetime import date, datetime
from os import listdir, path
from typing import List

from celery_app import r
from src.constants import DATETIME_PATTERN, DATETIME_FORMAT, DATE_PATTERN, DATE_FORMAT
from src.processors.constants import EARLIEST
from src.types import (
    JobType,
    SourceType,
    job_file_formats,
    job_paths,
    type_stubs,
    source_job_file_templates,
)
from src.util import get_datetime_string_from_string, get_date_string_from_string


class Processor:
    job_type: JobType
    source_type: SourceType
    redis_key: str

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}::{self.job_type.name}")

    def construct_filename_for_date(
        self, date: str, job_type: JobType | None = None
    ) -> str:
        if not job_type:
            job_type = self.job_type
        file_name_template = source_job_file_templates[self.source_type][job_type]
        file_format = job_file_formats[job_type]
        return file_name_template.format(date, file_format)

    def construct_filepath_for_date(
        self, date: str, job_type: JobType | None = None
    ) -> str:
        if not job_type:
            job_type = self.job_type
        file_name = self.construct_filename_for_date(date, job_type)
        dir_path = job_paths[job_type]
        return str(os.path.join(dir_path, file_name))

    def gather_dates(self, dir_path: str) -> list:
        """
        Get all dates from file names in a specified directory, optionally
         using a pattern to filter file names down to a particular set
        """
        file_name_stub = type_stubs.get(self.source_type, "")
        files = [
            f
            for f in listdir(dir_path)
            if path.isfile(os.path.join(dir_path, f))
            if file_name_stub in f
        ]
        dates = []
        for f in files:
            datetime_match = re.search(DATETIME_PATTERN, f)
            if datetime_match:
                date_str = datetime_match.group(0)
                dates.append(date_str)
            else:
                date_match = re.search(DATE_PATTERN, f)
                if date_match:
                    date_str = date_match.group(0)
                    dates.append(date_str)
        return sorted(dates)

    def gather_input_dates(self) -> List:
        raise NotImplementedError

    def gather_output_dates(self) -> List:
        raise NotImplementedError

    def extract_date_or_datetime(self, text: str) -> datetime | date | None:
        datetime_str = get_datetime_string_from_string(text)
        if datetime_str:
            try:
                return datetime.strptime(datetime_str, DATETIME_FORMAT)
            except ValueError as e:
                self.logger.debug(f"Could not parse datetime from {text}: {e}")

        date_str = get_date_string_from_string(text)
        if date_str:
            try:
                return datetime.strptime(date_str, DATE_FORMAT).date()
            except ValueError as e:
                self.logger.debug(f"Could not parse date from {text}: {e}")
        return None

    def extract_datetime_object(self, text: str) -> datetime | None:
        parsed = self.extract_date_or_datetime(text)
        if parsed is None:
            return None
        if isinstance(parsed, datetime):
            return parsed
        return datetime.combine(parsed, datetime.min.time())

    def get_most_recent_missing_dates(self) -> List[str]:
        input_dates = sorted(self.gather_input_dates())
        output_dates = sorted(self.gather_output_dates())
        return sorted(list(set(input_dates) - set(output_dates)))

    def clean_up(self) -> None:
        self.logger.info("No clean up needed")

    def process_for_date(self, date: str) -> None:
        raise NotImplementedError

    def process(self) -> None:
        missing_dates = self.get_most_recent_missing_dates()
        if not missing_dates:
            self.logger.info(f"Skipping {self.job_type.name}, no dates to process")
        self.logger.info(f"Missing dates for job {self.job_type}: {missing_dates}")
        for missing_date in missing_dates:
            try:
                dt = None
                datetime_str = get_datetime_string_from_string(missing_date)
                if datetime_str:
                    dt = datetime.strptime(datetime_str, DATETIME_FORMAT)
                date_str = get_date_string_from_string(missing_date)
                if date_str:
                    dt = datetime.strptime(date_str, DATE_FORMAT)
                if dt and dt < EARLIEST:
                    continue
                self.process_for_date(missing_date)
                r.hset(self.redis_key, missing_date, 1)
            except Exception as e:
                raise Exception(
                    f"Could not parse missing date for job {self.job_type}: {e}"
                )

        self.logger.info("Done.")
        self.clean_up()
