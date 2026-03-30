import os

from celery_app import r
from src.types import (
    JobType,
    SourceType,
    job_file_formats,
    job_paths,
    source_file_templates,
)
from src.util import get_most_recent_missing_dates


class Processor:
    input_dir: str
    output_dir: str
    job_type: JobType
    source_type: SourceType
    redis_key: str

    def __init__(
        self,
        input_dir: str,
        output_dir: str,
        job_type: JobType,
        source_type: SourceType,
        redis_key: str,
    ):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.job_type = job_type
        self.source_type = source_type
        self.redis_key = redis_key

    def construct_filename_for_date(self, date: str) -> str:
        file_name_template = source_file_templates[self.source_type]
        file_format = job_file_formats[self.job_type]
        return file_name_template.format(date, file_format)

    def construct_filepath_for_date(self, date: str) -> str:
        file_name = self.construct_filename_for_date(date)
        dir_path = job_paths[self.job_type]
        return str(os.path.join(dir_path, file_name))

    def process_for_date(self, input_filepath: str, output_filepath: str) -> None:
        raise NotImplementedError

    def process(self) -> None:
        missing_transcription_dates = get_most_recent_missing_dates(
            self.input_dir, self.output_dir, self.source_type
        )

        for missing_date in missing_transcription_dates:
            input_file_path = self.construct_filepath_for_date(missing_date)
            output_file_path = self.construct_filepath_for_date(missing_date)

            self.process_for_date(input_file_path, output_file_path)
            r.hset(self.redis_key, missing_date, 1)
