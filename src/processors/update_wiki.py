import atexit
from datetime import datetime
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from typing import Any, List, Mapping

from celery_app import r
import pywikibot
from pywikibot.textlib import Section

from src.constants import (
    WIKI_UPDATED_KEY,
    DETAIL_KEY,
    NO_ASSETS_KEY,
    DATETIME_FORMAT,
    DATE_FORMAT,
)
from src.processors.constants import (
    WIKI_MTG_TABLE_DATA,
    WIKI_AI_SECTION,
    WIKI_MTG_SECTION_TITLE,
    WIKI_MTG_TABLE_OPEN,
    WIKI_MTG_TABLE_CLOSE,
    WIKI_MTG_CANCELLED,
    WIKI_NO_MATERIALS_PAGE,
    WIKI_NO_MATERIALS_INTRO,
    WIKI_NO_MATERIALS_EMPTY,
    NATURAL_DATETIME_FORMAT,
    NATURAL_DATE_FORMAT,
    WIKI_AI_SECTION_TITLE,
)
from src.processors.process import Processor
from src.scrapers.models import PipelineClass
from src.settings import TRANSCRIBED_DIR
from src.types import JobType, SourceType, WikiMeeting, MEETING_TYPE_BY_SOURCE
from src.util import (
    get_year_string_from_string,
    get_date_or_datetime_string_from_string,
    get_date_string_from_string,
    send_to_discord_bots,
)


def _humanize_meeting_key(key: str) -> str:
    """Render a meeting key ('2026-06-03 07_00 PM' or '2026-06-03') for display."""
    for fmt, out in (
        (DATETIME_FORMAT, NATURAL_DATETIME_FORMAT),
        (DATE_FORMAT, NATURAL_DATE_FORMAT),
    ):
        try:
            return datetime.strptime(key, fmt).strftime(out)
        except ValueError:
            continue
    return key


def render_meeting_table_row(
    meeting_details: Mapping[str, Any],
    video_backups: list[str],
    doc_links: Mapping[str, str] | None = None,
) -> str:
    """Build the ` || `-joined wikitable cells from whichever assets exist.

    Only present assets get a cell, so a docs-only meeting renders cleanly with
    no empty `[None Video]`/`[None Transcript]` cells. When a durable archived
    link exists for a document (``doc_links`` keyed by label), it is used in
    place of the expiring CivicClerk source URL.
    """
    links = dict(doc_links or {})
    cells: list[str] = []
    agenda = meeting_details.get("agenda")
    agenda_packet = meeting_details.get("agenda_packet")
    durable_packet = links.get("Agenda Packet")
    if agenda:
        # Agenda and Agenda Packet are the same file in CivicClerk; prefer the
        # durable copy for the Agenda cell too when they match.
        agenda_link = durable_packet if agenda == agenda_packet else None
        cells.append(f"[{agenda_link or agenda} Agenda]")
    video = meeting_details.get("video")
    if video:
        cells.append(f"[{video} Video]")
    if agenda_packet:
        cells.append(f"[{durable_packet or agenda_packet} Agenda Packet]")
    for name, url in (
        meeting_details.get("minutes_and_supplemental_materials") or {}
    ).items():
        durable = links.get(name) or url
        if durable:
            cells.append(f"[{durable} {name}]")
    transcript = meeting_details.get("transcript_link")
    if transcript:
        cells.append(f"[{transcript} Transcript]")
    for i, link in enumerate(video_backups, start=1):
        cell = f"[{link} Video Backup]"
        if i > 1:
            cell = cell.replace("Backup", f"Backup part {i}")
        cells.append(cell)
    return " || ".join(cells)


def render_no_materials_page(entries: list[tuple[str, str]]) -> str:
    """Build the transparency page body from (meeting-type display, key) entries.

    One combined page across all meeting types; each row is labelled with its
    type so a City Council and a Planning Commission meeting on the same date are
    distinguishable.
    """
    if not entries:
        return WIKI_NO_MATERIALS_EMPTY
    rows = "\n".join(
        f"* {_humanize_meeting_key(key)} — {display}"
        for display, key in sorted(entries, key=lambda entry: entry[1], reverse=True)
    )
    return f"{WIKI_NO_MATERIALS_INTRO}\n\n{rows}\n"


class WikiUpdater(Processor):
    def __init__(
        self, source_type: SourceType = SourceType.CITY_COUNCIL_MEETING
    ) -> None:
        self.job_type = JobType.UPDATE_WIKI
        self.source_type = source_type
        self.meeting_type = MEETING_TYPE_BY_SOURCE[source_type]
        self.redis_key = self.meeting_type.redis_key(WIKI_UPDATED_KEY)
        self.video_backup_links: dict[str, str] = {}
        self.transcript_links: dict[str, str] = {}
        self.input_keys: list[str] = []
        self.output_keys: list[str] = []
        super().__init__()
        self.site = self.authenticate()

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

        b_keys = sorted(
            list(r.scan_iter(match=f"video_link.{self.meeting_type.key}.*"))
        )
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

    def get_doc_links_for_key(self, meeting_key: str) -> dict[str, str]:
        """Durable archived document links ({label: Drive link}) for a meeting."""
        raw = r.hgetall(
            self.meeting_type.doc_link_key_template.format(meeting_key=meeting_key)
        )
        return {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (
                v.decode("utf-8") if isinstance(v, bytes) else v
            )
            for k, v in (raw or {}).items()
        }

    def get_transcript_link(self, date: str) -> str | None:
        link = self.transcript_links.get(date)
        if link:
            return link

        res = list(r.scan_iter(match=f"transcript_link.{self.meeting_type.key}.*"))
        for b_redis_key in res:
            redis_key = b_redis_key.decode("utf-8")
            redis_date = redis_key.split(".")[2]
            if redis_date == date:
                b_link = r.get(redis_key)
                return b_link.decode("utf-8") if b_link else ""

        return None

    def gather_meeting_details(self, date: str) -> WikiMeeting:
        details_str: bytes | None = r.hget(
            self.meeting_type.redis_key(DETAIL_KEY), date
        )
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
        """Meetings eligible for a year-page entry: video meetings (via their
        transcript files) plus docs-only meetings (from detail), so a docs-only
        meeting gets an entry even though it has no transcript.
        """
        transcribed = self.gather_dates(TRANSCRIBED_DIR)
        return sorted(set(transcribed) | set(self._gather_docs_only_keys()))

    def _gather_docs_only_keys(self) -> list[str]:
        keys: list[str] = []
        for _field, raw in (
            r.hgetall(self.meeting_type.redis_key(DETAIL_KEY)) or {}
        ).items():
            try:
                detail = json.loads(raw.decode("utf-8"))
            except (ValueError, AttributeError):
                continue
            if detail.get(
                "pipeline_class"
            ) == PipelineClass.DOCS_ONLY.value and detail.get("key"):
                keys.append(detail["key"])
        return keys

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
            self.meeting_type.wiki_year_template.format(year) for year in input_years
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
        title = WIKI_MTG_SECTION_TITLE.format(
            meeting_key=self.derive_correct_date_header(key)
        )
        # A cancelled meeting reads "Cancelled" instead of an asset table (there
        # were no proceedings), and gets no AI-summary section. The city's
        # cancellation notice is still linked when one was published.
        if meeting_details.get("cancelled"):
            notice_links = render_meeting_table_row(
                meeting_details,
                [],  # cancelled meetings have no video backups
                doc_links=self.get_doc_links_for_key(key),
            )
            marker = WIKI_MTG_CANCELLED.strip()
            body = f"{marker} — {notice_links}" if notice_links else marker
            return [Section(title=title, content=f"\n{body}\n")]
        table_data = render_meeting_table_row(
            meeting_details,
            self.get_video_backup_links_for_key(key),
            doc_links=self.get_doc_links_for_key(key),
        )
        content = " ".join(
            [
                WIKI_MTG_TABLE_OPEN,
                WIKI_MTG_TABLE_DATA.format(table_data=table_data),
                WIKI_MTG_TABLE_CLOSE,
            ]
        )
        sections = [Section(title=title, content=content)]

        # Only meetings with a transcript get an AI-summary section.
        transcript_link = meeting_details.get("transcript_link")
        if transcript_link:
            sections.append(
                Section(
                    title=WIKI_AI_SECTION_TITLE,
                    content=WIKI_AI_SECTION.format(
                        ai_summary_text="TBD", transcript_link=transcript_link
                    ),
                )
            )
        return sections

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

        page = self.meeting_type.wiki_year_template.format(
            get_year_string_from_string(date)
        )
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
        send_to_discord_bots(f"{self.job_type.name} completed for {date}")

    def update_transparency_page(self) -> None:
        """Regenerate the single combined 'Meetings Without Published Materials'
        page from every configured type's no-asset set. Idempotent: only saves
        when the body changes, so meetings that later publish materials fall off
        automatically.
        """
        entries: list[tuple[str, str]] = []
        for meeting_type in MEETING_TYPE_BY_SOURCE.values():
            raw = r.smembers(meeting_type.redis_key(NO_ASSETS_KEY)) or set()
            for member in raw:
                key = member.decode("utf-8") if isinstance(member, bytes) else member
                entries.append((meeting_type.display_name, key))
        body = render_no_materials_page(entries)
        page = pywikibot.Page(self.site, WIKI_NO_MATERIALS_PAGE)
        if page.text.strip() != body.strip():
            page.text = body
            page.save(summary="Updated meetings without published materials")
            self.logger.info(
                "Updated transparency page with %d meeting(s)", len(entries)
            )

    def process(self) -> None:
        super().process()
        self.update_transparency_page()
