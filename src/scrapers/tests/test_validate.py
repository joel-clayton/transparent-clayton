import unittest

import requests

from src.scrapers.errors import TransientScrapeError
from src.scrapers.validate import url_resolves, url_resolves_best_effort


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSession:
    """Records calls and returns queued responses; raises if configured to."""

    def __init__(self, head=None, get=None, raise_exc=None):
        self._head = head
        self._get = get
        self._raise = raise_exc
        self.head_calls = 0
        self.get_calls = 0

    def head(self, url, **kwargs):
        self.head_calls += 1
        if self._raise:
            raise self._raise
        return self._head

    def get(self, url, **kwargs):
        self.get_calls += 1
        return self._get


class TestUrlResolves(unittest.TestCase):
    def test_returns_true_on_2xx_head(self):
        session = _FakeSession(head=_FakeResponse(200))
        self.assertTrue(url_resolves("https://x/y.mp4", session=session))
        self.assertEqual(session.get_calls, 0)  # no fallback needed

    def test_returns_false_on_404(self):
        session = _FakeSession(head=_FakeResponse(404))
        self.assertFalse(url_resolves("https://x/y.mp4", session=session))

    def test_falls_back_to_ranged_get_when_head_rejected(self):
        session = _FakeSession(head=_FakeResponse(405), get=_FakeResponse(206))
        self.assertTrue(url_resolves("https://x/y.mp4", session=session))
        self.assertEqual(session.get_calls, 1)

    def test_get_fallback_can_still_fail(self):
        session = _FakeSession(head=_FakeResponse(403), get=_FakeResponse(403))
        self.assertFalse(url_resolves("https://x/y.mp4", session=session))

    def test_raises_transient_on_network_error(self):
        session = _FakeSession(raise_exc=requests.ConnectionError("boom"))
        with self.assertRaises(TransientScrapeError):
            url_resolves("https://x/y.mp4", session=session)


class TestUrlResolvesBestEffort(unittest.TestCase):
    def test_swallows_transient_into_false(self):
        session = _FakeSession(raise_exc=requests.Timeout("slow"))
        self.assertFalse(url_resolves_best_effort("https://x/y.pdf", session=session))

    def test_passes_through_true(self):
        session = _FakeSession(head=_FakeResponse(200))
        self.assertTrue(url_resolves_best_effort("https://x/y.pdf", session=session))


if __name__ == "__main__":
    unittest.main()
