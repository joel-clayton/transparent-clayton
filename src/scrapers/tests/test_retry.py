import unittest

from src.scrapers.errors import SiteStructureError, TransientScrapeError
from src.scrapers.retry import retry_transient


class _FakeClock:
    """Deterministic sleep/monotonic pair for backoff tests."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class TestRetryTransient(unittest.TestCase):
    def test_returns_first_success_without_sleeping(self):
        clock = _FakeClock()
        calls = []

        def op():
            calls.append(1)
            return "ok"

        result = retry_transient(
            op, label="t", sleep=clock.sleep, monotonic=clock.monotonic
        )
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock.slept, [])

    def test_retries_then_succeeds(self):
        clock = _FakeClock()
        attempts = {"n": 0}

        def op():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TransientScrapeError("flaky")
            return "recovered"

        result = retry_transient(
            op, label="t", attempts=3, sleep=clock.sleep, monotonic=clock.monotonic
        )
        self.assertEqual(result, "recovered")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(clock.slept), 2)

    def test_exhausts_attempts_and_raises_transient(self):
        clock = _FakeClock()

        def op():
            raise TransientScrapeError("still down")

        with self.assertRaises(TransientScrapeError):
            retry_transient(
                op, label="t", attempts=3, sleep=clock.sleep, monotonic=clock.monotonic
            )
        self.assertEqual(len(clock.slept), 2)  # slept between the 3 attempts

    def test_does_not_retry_structural_errors(self):
        clock = _FakeClock()
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            raise SiteStructureError("markup changed")

        with self.assertRaises(SiteStructureError):
            retry_transient(op, label="t", sleep=clock.sleep, monotonic=clock.monotonic)
        self.assertEqual(calls["n"], 1)

    def test_stops_when_next_backoff_would_exceed_deadline(self):
        clock = _FakeClock()

        def op():
            raise TransientScrapeError("down")

        with self.assertRaises(TransientScrapeError):
            retry_transient(
                op,
                label="t",
                attempts=10,
                base_delay=100.0,
                max_delay=100.0,
                deadline=10.0,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )
        # First backoff (>=50s) already exceeds the 10s deadline -> never sleeps.
        self.assertEqual(clock.slept, [])


if __name__ == "__main__":
    unittest.main()
