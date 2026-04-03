import os
import re
from os import listdir, path
from typing import List

from celery_app import r
from src.types import (
    JobType,
    SourceType,
    job_file_formats,
    job_paths,
    source_file_templates,
    type_stubs,
)


class Processor:
    job_type: JobType
    source_type: SourceType
    redis_key: str

    def construct_filename_for_date(
        self, date: str, job_type: JobType | None = None
    ) -> str:
        file_name_template = source_file_templates[self.source_type]
        if not job_type:
            job_type = self.job_type
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
            date_match = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", f)
            if date_match:
                date_str = date_match.group(0)
                dates.append(date_str)
        return sorted(dates)

    def gather_input_dates(self) -> List:
        raise NotImplementedError

    def gather_output_dates(self) -> List:
        raise NotImplementedError

    def get_most_recent_missing_dates(self) -> List[str]:
        input_dates = sorted(self.gather_input_dates())
        output_dates = sorted(self.gather_output_dates())
        return sorted(list(set(input_dates) - set(output_dates)))

    def process_for_date(self, date: str) -> None:
        raise NotImplementedError

    def process(self) -> None:
        missing_transcription_dates = self.get_most_recent_missing_dates()

        for missing_date in missing_transcription_dates:
            self.process_for_date(missing_date)
            r.hset(self.redis_key, missing_date, 1)
