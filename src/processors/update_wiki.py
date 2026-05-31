import atexit
from datetime import datetime
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from typing import List

from celery_app import r
import pywikibot
from pywikibot.textlib import Section

from src.constants import WIKI_UPDATED_CC_MTG_KEY, DETAIL_CC_MTG_KEY, DATETIME_FORMAT
from src.processors.constants import (
    CC_MTG_WIKI_YEAR_NAME_TEMPLATE,
    WIKI_MTG_TABLE_DATA,
    WIKI_AI_SECTION,
    WIKI_MTG_SECTION_TITLE,
    WIKI_MTG_TABLE_OPEN,
    WIKI_MTG_TABLE_CLOSE,
    NATURAL_DATETIME_FORMAT,
    NATURAL_DATE_FORMAT,
    WIKI_AI_SECTION_TITLE,
)
from src.processors.process import Processor
from src.settings import TRANSCRIBED_DIR
from src.types import JobType, SourceType, WikiMeeting
from src.util import (
    get_year_string_from_string,
    get_date_or_datetime_string_from_string,
    get_date_string_from_string,
)


class WikiUpdater(Processor):
    def __init__(self) -> None:
        self.job_type = JobType.UPDATE_WIKI
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = WIKI_UPDATED_CC_MTG_KEY
        self.site = self.authenticate()
        self.video_backup_links: dict[str, str] = {}
        self.transcript_links: dict[str, str] = {}
        self.input_keys: list[str] = []
        self.output_keys: list[str] = []
        super().__init__()

    def authenticate(self) -> "pywikibot.site.APISite":
        url = os.environ.get("WIKI_URL")
        user = os.environ.get("WIKI_USERNAME")
        password = os.environ.get("WIKI_PASSWORD")
        if not all([url, user, password]):
            sys.exit("Set WIKI_URL, WIKI_USERNAME, WIKI_PASSWORD environment variables")

        # pywikibot insists on reading user-config.py / user-password.py from disk
        # before it will log in. We write minimal versions into a temp dir and point
        # PYWIKIBOT_DIR at it. The dir is cleaned up on exit.
        config_dir = tempfile.mkdtemp(prefix="pwb_")
        atexit.register(shutil.rmtree, config_dir, ignore_errors=True)
        os.environ["PYWIKIBOT_DIR"] = config_dir

        with open(os.path.join(config_dir, "user-config.py"), "w") as f:
            f.write("password_file = 'user-password.py'\n")
        pw_path = os.path.join(config_dir, "user-password.py")
        with open(pw_path, "w") as f:
            f.write(f"({user!r}, {password!r})\n")
        os.chmod(pw_path, 0o600)

        import pywikibot  # noqa: E402  (must come after PYWIKIBOT_DIR is set)
        from pywikibot import config as pwb_config  # noqa: E402

        # First Site() resolves the auto-generated family name from the URL.
        site = pywikibot.Site(url=url)
        pwb_config.usernames[site.family.name] = {site.code: user}

        # Re-instantiate so the registered username is bound to this site.
        site = pywikibot.Site(url=url)
        site.login()
        self.logger.info(
            f"Logged in as {site.user()} on {site.family.name}:{site.code}"
        )
        return site

    def get_sections_from_wiki_page(self, page_name: str) -> list:
        from pywikibot import textlib

        page = pywikibot.Page(self.site, page_name)
        if page.exists():
            return textlib.extract_sections(page.text, self.site).sections

        return []

    def retrieve_video_backup_links(self) -> dict[str, str]:
        if self.video_backup_links:
            return self.video_backup_links

        b_keys = sorted(list(r.scan_iter(match="video_link.*")))
        b_values = r.mget(b_keys)
        for b_redis_key, b_value in zip(b_keys, b_values):
            if not b_value:
                self.logger.error(f"Could not find video link for {b_redis_key!r}")
                continue
            redis_key = b_redis_key.decode("utf-8")
            date = redis_key.split(".")[2]

            part_num = int(redis_key.split(".")[3])
            if part_num > 1:
                date += f" part {part_num}"

            self.video_backup_links[date] = b_value.decode("utf-8")
        return self.video_backup_links

    def get_video_backup_links_for_key(self, meeting_key: str) -> list[str]:
        if not self.video_backup_links:
            self.retrieve_video_backup_links()

        matches = []
        for meeting, link in self.video_backup_links.items():
            if meeting_key == get_date_or_datetime_string_from_string(meeting):
                matches.append(link)

        return matches

    def get_transcript_link(self, date: str) -> str | None:
        link = self.transcript_links.get(date)
        if link:
            return link

        res = list(r.scan_iter(match="transcript_link.*"))
        for b_redis_key in res:
            redis_key = b_redis_key.decode("utf-8")
            redis_date = redis_key.split(".")[2]
            if redis_date == date:
                b_link = r.get(redis_key)
                return b_link.decode("utf-8") if b_link else ""

        return None

    def gather_meeting_details(self, date: str) -> WikiMeeting:
        details_str: bytes | None = r.hget(DETAIL_CC_MTG_KEY, date)
        if not details_str:
            raise Exception(
                f"Could not find meeting details for {self.source_type}-- {date}"
            )
        details = json.loads(details_str.decode("utf-8"))

        details["video_backup_links"] = self.get_video_backup_links_for_key(date)
        details["transcript_link"] = self.get_transcript_link(date)
        wiki_meeting: WikiMeeting = details
        return wiki_meeting

    def derive_correct_date_header(self, key: str) -> str:
        dt = datetime.strptime(key, DATETIME_FORMAT)
        input_dates_counter = Counter(
            [get_date_string_from_string(k) for k in self.input_keys]
        )
        if input_dates_counter.get(key, 1) > 1:
            return dt.strftime(NATURAL_DATETIME_FORMAT)
        return dt.strftime(NATURAL_DATE_FORMAT)

    def gather_input_dates(self) -> List:
        """
        from Transcripts folder
        """
        return self.gather_dates(TRANSCRIBED_DIR)

    def gather_output_dates(self) -> List:
        """
        Gather years for the last 6 months
        Read year pages if they exist
        Parse dates out of text from table headers
        if dates for calculated output dates, create a year page
        """
        input_dates = self.gather_input_dates()
        input_years = sorted(
            set([get_year_string_from_string(input_date) for input_date in input_dates])
        )
        pages_to_scrape = [
            CC_MTG_WIKI_YEAR_NAME_TEMPLATE.format(year) for year in input_years
        ]

        dates = []
        for page in pages_to_scrape:
            sections = self.get_sections_from_wiki_page(page)
            for section in sections:
                key = get_date_or_datetime_string_from_string(section.title)
                if key:
                    dates.append(key)
        return sorted(dates)

    def get_most_recent_missing_dates(self) -> List[str]:
        """
        Deals in meeting keys, i.e. full datetimes, when available
        :return:
        """
        self.input_keys = sorted(self.gather_input_dates())
        self.output_keys = sorted(self.gather_output_dates())

        input_dates = [get_date_string_from_string(d) for d in self.input_keys]
        dates = []
        for key, date in zip(self.input_keys, input_dates):
            if key in self.output_keys or date in self.output_keys:
                continue
            dates.append(key)

        return dates

    def format_wiki_section(self, meeting_details: WikiMeeting) -> List[Section]:
        key = meeting_details.get("key")
        if not key:
            raise Exception(
                f"key missing from meeting_details object {meeting_details}"
            )
        table_items = [
            f"[{meeting_details.get('agenda')} Agenda]",
            f"[{meeting_details.get('video')} Video]",
            f"[{meeting_details.get('agenda_packet')} Agenda Packet]",
            f"[{meeting_details.get('transcript_link')} Transcript]",
        ]
        video_links = self.get_video_backup_links_for_key(key)
        if not video_links:
            raise Exception(f"Video links not found for {key}")
        for i, link in enumerate(video_links, start=1):
            video_cell = f"[{link} Video Backup]"
            if i > 1:
                video_cell = video_cell.replace("Backup", f"Backup part {i}")
            table_items.append(video_cell)
        table_data = " || ".join(table_items)

        title = WIKI_MTG_SECTION_TITLE.format(
            meeting_key=self.derive_correct_date_header(key)
        )

        content = " ".join(
            [
                WIKI_MTG_TABLE_OPEN,
                WIKI_MTG_TABLE_DATA.format(table_data=table_data),
                WIKI_MTG_TABLE_CLOSE,
            ]
        )
        ai_title = WIKI_AI_SECTION_TITLE
        ai_content = WIKI_AI_SECTION.format(
            ai_summary_text="TBD",
            transcript_link=meeting_details.get("transcript_link"),
        )
        return [
            Section(title=title, content=content),
            Section(title=ai_title, content=ai_content),
        ]

    def update_page_sections_for_page(
        self, page_name: str, sections: List[Section], date: str
    ) -> None:
        page = pywikibot.Page(self.site, page_name)
        if page.exists():
            page.text = "".join(f"{s.title}{s.content}" for s in sections)
            page.save(summary=f"Added new meeting: {date}")

    def process_for_date(self, date: str) -> None:
        meeting_details = self.gather_meeting_details(date)
        new_section_group = self.format_wiki_section(meeting_details)
        new_section_title = self.derive_correct_date_header(date)

        page = CC_MTG_WIKI_YEAR_NAME_TEMPLATE.format(get_year_string_from_string(date))
        current_sections = self.get_sections_from_wiki_page(page)

        dates = [
            get_date_or_datetime_string_from_string(s.title)
            for s in current_sections
            if get_date_or_datetime_string_from_string(s.title)
        ]
        dates.append(new_section_title)
        dates = sorted(dates, reverse=True)

        # Find the correct index for ordering sections by date
        if dates.index(new_section_title) + 1 == len(dates):
            new_page_sections = current_sections + new_section_group
        else:
            next_date = dates[dates.index(new_section_title) + 1]

            def contains_date(x: str) -> bool:
                return get_date_or_datetime_string_from_string(x) == next_date

            first_index = next(
                (i for i, x in enumerate(current_sections) if contains_date(x.title)),
                None,
            )
            new_page_sections = (
                current_sections[:first_index]
                + new_section_group
                + current_sections[first_index:]
            )

        if new_page_sections:
            self.update_page_sections_for_page(page, new_page_sections, date)
        self.logger.info(f"processed wiki update for {date}")
