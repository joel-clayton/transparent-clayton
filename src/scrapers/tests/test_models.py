import unittest

from pydantic import ValidationError

from src.scrapers.models import (
    CITY_COUNCIL_MEETING_SOURCE,
    MeetingRecord,
    PipelineClass,
    ScrapeStatus,
)


class TestMeetingRecordValidation(unittest.TestCase):
    def _valid_kwargs(self, **overrides):
        kwargs = dict(
            key="2026-06-03 07_00 PM",
            clip_id="12345",
            video="https://cpmedia.azureedge.net/clip.mp4",
        )
        kwargs.update(overrides)
        return kwargs

    def test_minimal_valid_record(self):
        record = MeetingRecord(**self._valid_kwargs())
        self.assertEqual(record.clip_id, "12345")
        self.assertEqual(record.source_type, CITY_COUNCIL_MEETING_SOURCE)
        self.assertEqual(record.status, ScrapeStatus.COMPLETE)

    def test_accepts_date_only_key(self):
        record = MeetingRecord(**self._valid_kwargs(key="2026-06-03"))
        self.assertEqual(record.key, "2026-06-03")

    def test_rejects_unparseable_key(self):
        with self.assertRaises(ValidationError):
            MeetingRecord(**self._valid_kwargs(key="June 3rd"))

    def test_rejects_empty_key(self):
        with self.assertRaises(ValidationError):
            MeetingRecord(**self._valid_kwargs(key="   "))

    def test_rejects_missing_clip_id(self):
        with self.assertRaises(ValidationError):
            MeetingRecord(**self._valid_kwargs(clip_id=""))

    def test_rejects_non_http_url(self):
        with self.assertRaises(ValidationError):
            MeetingRecord(**self._valid_kwargs(video="ftp://example.com/x.mp4"))

    def test_empty_video_marks_pending(self):
        record = MeetingRecord(**self._valid_kwargs(video=""))
        self.assertEqual(record.status, ScrapeStatus.PENDING_VIDEO)

    def test_explicit_quarantine_is_preserved(self):
        record = MeetingRecord(**self._valid_kwargs(status=ScrapeStatus.QUARANTINED))
        self.assertEqual(record.status, ScrapeStatus.QUARANTINED)

    def test_strips_whitespace_on_key_and_urls(self):
        record = MeetingRecord(
            **self._valid_kwargs(key="  2026-06-03  ", video="  https://x/y.mp4 ")
        )
        self.assertEqual(record.key, "2026-06-03")
        self.assertEqual(record.video, "https://x/y.mp4")


class TestMeetingRecordSerialization(unittest.TestCase):
    def test_model_dump_matches_wire_contract(self):
        record = MeetingRecord(
            key="2026-06-03 07_00 PM",
            clip_id="98765",
            video="https://cpmedia.azureedge.net/clip.mp4",
            agenda="https://example.com/agenda.pdf",
            agenda_packet="https://example.com/agenda.pdf",
            minutes_and_supplemental_materials={"Staff Report": "https://x/s.pdf"},
        )
        dumped = record.model_dump(mode="json")
        # Keys the downstream Meeting TypedDict / consumers rely on.
        for field in (
            "key",
            "duration",
            "agenda",
            "minutes_and_supplemental_materials",
            "video",
            "agenda_packet",
            "clip_id",
            "source_type",
        ):
            self.assertIn(field, dumped)
        self.assertEqual(dumped["source_type"], "city_council_meeting")
        self.assertEqual(dumped["status"], "complete")
        self.assertEqual(
            dumped["minutes_and_supplemental_materials"],
            {"Staff Report": "https://x/s.pdf"},
        )

    def test_ignores_unknown_legacy_keys(self):
        record = MeetingRecord(
            key="2026-06-03",
            clip_id="1",
            video="",
            some_removed_legacy_field="whatever",
        )
        self.assertFalse(hasattr(record, "some_removed_legacy_field"))


class TestPipelineClass(unittest.TestCase):
    def _record(self, **overrides):
        kwargs = dict(key="2026-06-03", clip_id="1", video="")
        kwargs.update(overrides)
        return MeetingRecord(**kwargs)

    def test_video_classifies_full(self):
        record = self._record(video="https://x/y.mp4")
        self.assertEqual(record.pipeline_class, PipelineClass.FULL)

    def test_video_wins_even_with_docs(self):
        record = self._record(video="https://x/y.mp4", agenda_packet="https://x/a.pdf")
        self.assertEqual(record.pipeline_class, PipelineClass.FULL)

    def test_docs_only_when_no_video(self):
        record = self._record(agenda_packet="https://x/a.pdf")
        self.assertEqual(record.pipeline_class, PipelineClass.DOCS_ONLY)

    def test_minutes_alone_is_docs_only(self):
        record = self._record(
            minutes_and_supplemental_materials={"Staff Report": "https://x/s.pdf"}
        )
        self.assertEqual(record.pipeline_class, PipelineClass.DOCS_ONLY)

    def test_no_assets_when_empty(self):
        record = self._record()
        self.assertEqual(record.pipeline_class, PipelineClass.NO_ASSETS)

    def test_pipeline_class_serialized(self):
        record = self._record(agenda_packet="https://x/a.pdf")
        self.assertEqual(record.model_dump(mode="json")["pipeline_class"], "docs_only")


if __name__ == "__main__":
    unittest.main()
