import logging
import os
import re
from datetime import date, datetime
from typing import List

from celery_app import r
from src.constants import DATETIME_PATTERN, DATETIME_FORMAT, DATE_PATTERN, DATE_FORMAT
from src.processors.constants import EARLIEST
from src.types import (
    JobType,
    SourceType,
    MEETING_TYPE_BY_SOURCE,
    job_file_formats,
    job_paths,
    type_stubs,
    source_job_file_templates,
)
from src.storage_layout import (
    iter_stage_files,
    resolve_existing_file,
    write_dir_for_bucket,
)
from src.scrapers.alerting import AlertLevel, alert
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
        base_dir = job_paths[job_type]
        bucket = MEETING_TYPE_BY_SOURCE[self.source_type].disk_bucket
        # Resolve against existing files (per-type bucket, then legacy flat during
        # the transition); when neither exists this is an imminent write, so ensure
        # the bucket dir. write_dir_for_bucket no-ops on an unmounted volume, so the
        # write still fails loudly rather than landing on the boot disk.
        resolved = resolve_existing_file(base_dir, bucket, file_name)
        if not os.path.exists(resolved):
            write_dir_for_bucket(base_dir, bucket)
        return str(resolved)

    def gather_dates(self, dir_path: str) -> list:
        """
        Get all dates from file names in a stage directory, filtered to this
        meeting type. Reads this type's per-type bucket plus the legacy flat
        layout (transition). Deduped with a set because the same date can appear in
        both layouts mid-transition; callers treat the result as a date set.
        """
        file_name_stub = type_stubs.get(self.source_type, "")
        bucket = MEETING_TYPE_BY_SOURCE[self.source_type].disk_bucket
        dates = []
        for full in iter_stage_files(dir_path, bucket):
            name = os.path.basename(full)
            if file_name_stub not in name:
                continue
            datetime_match = re.search(DATETIME_PATTERN, name)
            if datetime_match:
                dates.append(datetime_match.group(0))
            else:
                date_match = re.search(DATE_PATTERN, name)
                if date_match:
                    dates.append(date_match.group(0))
        return sorted(set(dates))

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
        self.logger.debug("No clean up needed")

    def log_complete_for_date(self, date: str) -> None:
        self.logger.info(f"{self.job_type} is complete for {date}")

    def process_for_date(self, date: str) -> None:
        raise NotImplementedError

    def process(self) -> None:
        missing_dates: List[str] = self.get_most_recent_missing_dates()
        self.logger.debug(f"Missing dates for job {self.job_type}: {missing_dates}")
        failures: List[str] = []
        for missing_date in missing_dates:
            try:
                datetime_str = get_datetime_string_from_string(missing_date)
                if datetime_str:
                    dt = datetime.strptime(datetime_str, DATETIME_FORMAT)
                else:
                    dt = datetime.strptime(
                        get_date_string_from_string(missing_date), DATE_FORMAT
                    )
                if dt < EARLIEST:
                    continue
                self.process_for_date(missing_date)
                # Mark done only after process_for_date succeeds; a raise below
                # leaves the date unmarked so the next run retries it.
                r.hset(self.redis_key, missing_date, 1)
            except Exception as e:
                # One meeting's failure must not abort the rest of the batch
                # (previously this re-raised, blocking every meeting behind it).
                # Log it, collect it, and continue; a batched alert is sent below.
                self.logger.error(
                    "Failed %s for %s: %s", self.job_type.name, missing_date, e
                )
                failures.append(f"{missing_date}: {e}")

        if failures:
            alert(
                AlertLevel.ACTIONABLE,
                f"{len(failures)} {self.job_type.name} meeting(s) failed this run:"
                "\n- " + "\n- ".join(failures),
            )
        self.clean_up()
        self.logger.info(f"{self.job_type} is Done")
