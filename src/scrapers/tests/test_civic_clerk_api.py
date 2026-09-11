import unittest
from datetime import datetime

import requests

from src.meeting_types import CITY_COUNCIL, PLANNING_COMMISSION
from src.scrapers import civic_clerk_api as api
from src.scrapers.errors import TransientScrapeError


def _event(**overrides):
    base = {
        "id": 7,
        "categoryName": "City Council",
        "startDateTime": "2026-07-07T19:00:00Z",
        "isOnDemandEvent": False,
        "mediaStreamPath": "",
        "publishedFiles": [],
    }
    base.update(overrides)
    return base


class _FakePage:
    """Minimal requests-like response for fetch_events pagination tests."""

    def __init__(self, value, next_link=None):
        self._body = {"value": value}
        if next_link:
            self._body["@odata.nextLink"] = next_link

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        return self._pages.pop(0)


class TestAssetDerivation(unittest.TestCase):
    def test_video_url_handles_relative_and_absolute_stream_paths(self):
        # Relative path: lowercased onto the CDN base.
        self.assertEqual(
            api.video_url(
                _event(isOnDemandEvent=True, mediaStreamPath="CLAYTONCA/AbC-123.MP4")
            ),
            "https://cpmedia.azureedge.net/claytonca/abc-123.mp4",
        )
        # Already-absolute path: returned untouched (not double-prefixed).
        absolute = "https://cpmedia.azureedge.net/claytonca/25ef6c75b9.mp4"
        self.assertEqual(
            api.video_url(_event(isOnDemandEvent=True, mediaStreamPath=absolute)),
            absolute,
        )
        # No video when not on-demand, or on-demand but no stream path.
        self.assertEqual(api.video_url(_event(mediaStreamPath="CLAYTONCA/x.mp4")), "")
        self.assertEqual(api.video_url(_event(isOnDemandEvent=True)), "")

    def test_split_documents_separates_agenda_packet_and_keys_the_rest(self):
        event = _event(
            publishedFiles=[
                {"fileId": 1, "type": "Agenda", "name": "Agenda Final"},
                {"fileId": 2, "type": "Agenda Packet", "name": "Packet"},
                {"fileId": 3, "type": "Attachment", "name": "Staff Report"},
                {"fileId": 4, "type": "Attachment", "name": "Staff Report"},
                {"fileId": 0, "type": "Attachment", "name": "Ignored (no id)"},
            ]
        )
        agenda_packet, supplemental = api.split_documents(event)
        self.assertEqual(
            agenda_packet,
            "https://claytonca.api.civicclerk.com/v1/Meetings/"
            "GetMeetingFileStream(fileId=2,plainText=false)",
        )
        # Non-packet files keyed by name; a shared name is suffixed, not lost;
        # a file with no fileId is skipped.
        self.assertEqual(
            set(supplemental),
            {"Agenda Final", "Staff Report", "Staff Report (2)"},
        )
        self.assertNotIn("Ignored (no id)", supplemental)

    def test_split_documents_empty_when_no_files(self):
        self.assertEqual(api.split_documents(_event()), ("", {}))


class TestClassificationHelpers(unittest.TestCase):
    def test_matches_category_is_exact(self):
        cc = _event(categoryName="City Council")
        self.assertTrue(api.matches_category(cc, CITY_COUNCIL))
        self.assertFalse(api.matches_category(cc, PLANNING_COMMISSION))

    def test_is_cancelled_from_agenda_name_prefix(self):
        # Spelling and case vary across the portal's cancellation markers.
        for agenda_name in (
            "CANCELED - Planning Commission Meeting",
            "Canceled - Planning Commission Meeting",
            "Cancelled - City Council",
        ):
            self.assertTrue(api.is_cancelled(_event(agendaName=agenda_name)))
        # A normal or missing agendaName is not cancelled.
        self.assertFalse(api.is_cancelled(_event(agendaName="City Council 070726")))
        self.assertFalse(api.is_cancelled(_event()))

    def test_event_datetime_strips_the_z_as_wall_clock_local(self):
        self.assertEqual(
            api.event_datetime(_event(startDateTime="2026-07-07T19:00:00Z")),
            datetime(2026, 7, 7, 19, 0),
        )

    def test_merge_unions_assets_of_same_datetime_events(self):
        # The portal splits one meeting across records: id=90 has the documents,
        # id=91 has the video, both at 7/7. A later meeting at 8/4 is separate.
        docs_record = _event(
            id=90,
            publishedFiles=[{"fileId": 1, "type": "Agenda Packet", "name": "Packet"}],
        )
        video_record = _event(
            id=91, isOnDemandEvent=True, mediaStreamPath="CLAYTONCA/v.mp4"
        )
        other = _event(id=9, startDateTime="2026-08-04T19:00:00Z")
        result = api.merge_by_datetime([docs_record, video_record, other])
        self.assertEqual([e["id"] for e in result], [91, 9])  # time-ordered
        merged = result[0]
        # Video record anchors the merged event; the docs record's file is folded
        # in, so the meeting classifies FULL *and* keeps its document.
        self.assertTrue(api.video_url(merged))
        agenda_packet, _ = api.split_documents(merged)
        self.assertTrue(agenda_packet)


class TestFetchEvents(unittest.TestCase):
    def test_follows_next_link_and_aggregates_values(self):
        session = _FakeSession(
            [
                _FakePage([{"id": 1}], next_link="https://api/next"),
                _FakePage([{"id": 2}]),
            ]
        )
        events = api.fetch_events(
            datetime(2026, 7, 1), datetime(2026, 9, 1), session=session
        )
        self.assertEqual([e["id"] for e in events], [1, 2])
        # First call carries the range filter; the second follows nextLink with
        # no extra params (the link already encodes the query).
        self.assertIn("$filter", session.calls[0][1])
        self.assertEqual(session.calls[1][0], "https://api/next")
        self.assertIsNone(session.calls[1][1])

    def test_network_failure_is_transient(self):
        class _Boom:
            def get(self, *a, **k):
                raise requests.ConnectionError("down")

        with self.assertRaises(TransientScrapeError):
            api.fetch_events(
                datetime(2026, 7, 1), datetime(2026, 9, 1), session=_Boom()
            )


if __name__ == "__main__":
    unittest.main()
