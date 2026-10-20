import json
import unittest
from unittest.mock import patch

from src.processors.helpers.agenda_items import AgendaItem
from src.processors.helpers import segmentation
from src.processors.helpers.segmentation import _ask_boundaries, segment_meeting
from src.processors.helpers.transcript import Utterance

ITEMS = [
    AgendaItem(1, "Call to Order", "CALL TO ORDER", True),
    AgendaItem(2, "Public Comment", "PUBLIC COMMENT", False),
    AgendaItem(3, "Public Hearings", "PUBLIC HEARINGS", False),
    AgendaItem(4, "Adjournment", "ADJOURNMENT", True),
]


def _utterances(times: list[int]) -> list[Utterance]:
    return [Utterance("S", t, t + 5, f"text at {t}") for t in times]


class TestSegmentResolution(unittest.TestCase):
    def _segment(self, detected: dict[int, int], times: list[int]):
        with patch.object(segmentation, "_ask_boundaries", return_value=detected):
            return {
                s.number: s
                for s in segment_meeting(ITEMS, _utterances(times), model="m")
            }

    def test_spans_and_unreached(self):
        segs = self._segment({1: 0, 2: 100, 4: 500}, [0, 100, 200, 500, 550])
        self.assertTrue(segs[1].reached)
        self.assertEqual((segs[1].t_start, segs[1].t_end), (0, 100))
        self.assertEqual((segs[2].t_start, segs[2].t_end), (100, 500))  # skips item 3
        self.assertFalse(segs[3].reached)  # not detected -> not taken up
        self.assertEqual(segs[3].utterances, [])
        self.assertTrue(segs[4].reached)
        self.assertEqual(segs[4].t_end, 555)  # to meeting end

    def test_monotonic_repair_pulls_up_regressions(self):
        # item 4 detected before item 2 -> clamped to the running floor, order kept.
        segs = self._segment({1: 0, 2: 100, 4: 50}, [0, 100, 200])
        self.assertGreaterEqual(segs[4].t_start, segs[2].t_start)

    def test_zero_length_collapses_to_not_reached(self):
        # items 2 and 3 share a boundary -> item 2 has no real span -> not reached.
        segs = self._segment({1: 0, 2: 100, 3: 100, 4: 500}, [0, 100, 500])
        self.assertFalse(segs[2].reached)
        self.assertEqual(segs[2].utterances, [])
        self.assertTrue(segs[3].reached)

    def test_empty_inputs(self):
        self.assertEqual(segment_meeting([], _utterances([0]), model="m"), [])
        self.assertEqual(segment_meeting(ITEMS, [], model="m"), [])


class TestAskBoundaries(unittest.TestCase):
    def test_parses_json_and_clamps_out_of_range(self):
        window = _utterances([100, 200, 300])  # range 100..300
        payload = json.dumps(
            {
                "boundaries": [
                    {"item": 1, "start": "0:03:20"},  # 200, in range
                    {"item": 2, "start": "9:00:00"},  # out of range -> dropped
                    {"item": 3, "start": None},  # null -> dropped
                    {"item": 99, "start": "0:02:00"},  # unknown item -> dropped
                ]
            }
        )
        with patch.object(segmentation, "generate", return_value=payload):
            out = _ask_boundaries(ITEMS, window, "m")
        self.assertEqual(out, {1: 200})

    def test_boundary_inside_last_utterance_is_kept(self):
        # Last utterance starts at 300, ends at 305; a boundary at 303 is valid and
        # must not be clamped out (regression: upper bound was the start, not end).
        window = _utterances([100, 200, 300])
        payload = json.dumps({"boundaries": [{"item": 2, "start": "0:05:03"}]})
        with patch.object(segmentation, "generate", return_value=payload):
            self.assertEqual(_ask_boundaries(ITEMS, window, "m"), {2: 303})

    def test_unparseable_json_returns_empty(self):
        with patch.object(segmentation, "generate", return_value="not json"):
            self.assertEqual(_ask_boundaries(ITEMS, _utterances([0]), "m"), {})


if __name__ == "__main__":
    unittest.main()
