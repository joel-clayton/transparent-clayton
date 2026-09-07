import unittest

from src import meeting_types as mt
from src.constants import (
    DETAIL_KEY,
    SCRAPED_KEY,
    DOWNLOADED_KEY,
    COMPRESSED_KEY,
    EXTRACTED_KEY,
    AUDIO_TRANSCRIBED_KEY,
    VIDEO_UPLOADED_KEY,
    TRANSCRIPT_UPLOADED_KEY,
    WIKI_UPDATED_KEY,
    COMPLETED_KEY,
    AV_SEEN_KEY,
    DOCS_ARCHIVED_KEY,
    NO_ASSETS_KEY,
    DETAIL_CC_MTG_KEY,
    SCRAPED_CC_MTG_KEY,
    DOWNLOADED_CC_MTG_KEY,
    COMPRESSED_CC_MTG_KEY,
    EXTRACTED_CC_MTG_KEY,
    AUDIO_TRANSCRIBED_CC_MTG_KEY,
    VIDEO_UPLOADED_CC_MTG_KEY,
    TRANSCRIPT_UPLOADED_CC_MTG_KEY,
    WIKI_UPDATED_CC_MTG_KEY,
    COMPLETED_CC_MTG_KEY,
    AV_SEEN_CC_MTG_KEY,
    DOCS_ARCHIVED_CC_MTG_KEY,
    NO_ASSETS_CC_MTG_KEY,
    VIDEO_LINK_CC_MTG_KEY_TEMPLATE,
    TRANSCRIPT_LINK_CC_MTG_KEY_TEMPLATE,
    DOC_LINK_CC_MTG_KEY_TEMPLATE,
    VIDEO_PLAYLIST_CC_MTG_KEY_TEMPLATE,
    VIDEO_PLAYLIST_NAME_TEMPLATE,
    CC_MTG_FILE_STUB,
)
from src.processors.constants import (
    CC_MTG_FILE_TEMPLATE,
    CC_MTG_FILE_TEMPLATE_YT_DLP,
    CC_MTG_FILE_TEMPLATE_COMPRESSED_SEGMENTED,
    CC_MTG_FILE_TEMPLATE_COMPRESSED_NOT_SEGMENTED,
    CC_MTG_VIDEO_TITLE_DATETIME_FORMAT,
    CC_MTG_VIDEO_TITLE_DATE_FORMAT,
    CC_MTG_TRANSCRIPT_TITLE_FORMAT,
    CC_MTG_TRANSCRIPT_TITLE_DATE_FORMAT,
    CC_MTG_WIKI_YEAR_NAME_TEMPLATE,
)
from src.scrapers.models import CITY_COUNCIL_MEETING_SOURCE


class TestCityCouncilReproducesConstants(unittest.TestCase):
    """The CC config must equal today's constants so migrating consumers is a
    no-op for existing data, filenames, wiki pages, and the YouTube channel."""

    def test_redis_namespace_keys(self):
        cc = mt.CITY_COUNCIL
        pairs = [
            (DETAIL_KEY, DETAIL_CC_MTG_KEY),
            (SCRAPED_KEY, SCRAPED_CC_MTG_KEY),
            (DOWNLOADED_KEY, DOWNLOADED_CC_MTG_KEY),
            (COMPRESSED_KEY, COMPRESSED_CC_MTG_KEY),
            (EXTRACTED_KEY, EXTRACTED_CC_MTG_KEY),
            (AUDIO_TRANSCRIBED_KEY, AUDIO_TRANSCRIBED_CC_MTG_KEY),
            (VIDEO_UPLOADED_KEY, VIDEO_UPLOADED_CC_MTG_KEY),
            (TRANSCRIPT_UPLOADED_KEY, TRANSCRIPT_UPLOADED_CC_MTG_KEY),
            (WIKI_UPDATED_KEY, WIKI_UPDATED_CC_MTG_KEY),
            (COMPLETED_KEY, COMPLETED_CC_MTG_KEY),
            (AV_SEEN_KEY, AV_SEEN_CC_MTG_KEY),
            (DOCS_ARCHIVED_KEY, DOCS_ARCHIVED_CC_MTG_KEY),
            (NO_ASSETS_KEY, NO_ASSETS_CC_MTG_KEY),
        ]
        for prefix, expected in pairs:
            with self.subTest(prefix=prefix):
                self.assertEqual(cc.redis_key(prefix), expected)

    def test_derived_strings_match_constants(self):
        cc = mt.CITY_COUNCIL
        pairs = [
            (cc.source_type, CITY_COUNCIL_MEETING_SOURCE),
            (cc.video_link_key_template, VIDEO_LINK_CC_MTG_KEY_TEMPLATE),
            (cc.transcript_link_key_template, TRANSCRIPT_LINK_CC_MTG_KEY_TEMPLATE),
            (cc.doc_link_key_template, DOC_LINK_CC_MTG_KEY_TEMPLATE),
            (cc.video_playlist_key_template, VIDEO_PLAYLIST_CC_MTG_KEY_TEMPLATE),
            (cc.playlist_name_template, VIDEO_PLAYLIST_NAME_TEMPLATE),
            (cc.file_stub, CC_MTG_FILE_STUB),
            (cc.file_template, CC_MTG_FILE_TEMPLATE),
            (cc.file_template_yt_dlp, CC_MTG_FILE_TEMPLATE_YT_DLP),
            (
                cc.compressed_segmented_template,
                CC_MTG_FILE_TEMPLATE_COMPRESSED_SEGMENTED,
            ),
            (
                cc.compressed_not_segmented_template,
                CC_MTG_FILE_TEMPLATE_COMPRESSED_NOT_SEGMENTED,
            ),
            (cc.video_title_datetime_format, CC_MTG_VIDEO_TITLE_DATETIME_FORMAT),
            (cc.video_title_date_format, CC_MTG_VIDEO_TITLE_DATE_FORMAT),
            (cc.transcript_title_datetime_format, CC_MTG_TRANSCRIPT_TITLE_FORMAT),
            (cc.transcript_title_date_format, CC_MTG_TRANSCRIPT_TITLE_DATE_FORMAT),
            (cc.wiki_year_template, CC_MTG_WIKI_YEAR_NAME_TEMPLATE),
        ]
        for derived, expected in pairs:
            with self.subTest(expected=expected):
                self.assertEqual(derived, expected)


class TestClassifyAndSecondType(unittest.TestCase):
    def test_classify_by_title_substring(self):
        cases = [
            ("Regular City Council Meeting", mt.CITY_COUNCIL),
            ("Planning Commission - Special Session", mt.PLANNING_COMMISSION),
            ("Trails and Recreation Committee", None),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertIs(mt.classify(title), expected)

    def test_planning_commission_derivations(self):
        pc = mt.PLANNING_COMMISSION
        self.assertEqual(pc.redis_key("detail"), "detail.pc_mtg")
        self.assertEqual(pc.source_type, "planning_commission_meeting")
        self.assertEqual(pc.file_stub, "Planning Commission Meeting")
        self.assertEqual(
            pc.wiki_year_template, "List of {} Planning Commission Meetings"
        )


if __name__ == "__main__":
    unittest.main()
