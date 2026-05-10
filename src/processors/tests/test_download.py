import json
import unittest
from unittest.mock import MagicMock, patch

from src.processors.download import Downloader


PLAYER_HTML_WITH_M3U8 = """
<html>
  <body>
    <script>
      var stream = 'https://archive-stream.example.com/clip/12345/playlist.m3u8?token=abc';
    </script>
  </body>
</html>
"""


class TestDownloaderGatherInputDates(unittest.TestCase):
    def test_returns_parsed_list_when_redis_has_value(self):
        downloader = Downloader()
        with patch("src.processors.download.r") as mock_r:
            mock_r.get.return_value = json.dumps(["2026-05-08", "2026-06-01"]).encode()
            self.assertEqual(
                downloader.gather_input_dates(), ["2026-05-08", "2026-06-01"]
            )

    def test_returns_empty_list_when_redis_empty(self):
        downloader = Downloader()
        with patch("src.processors.download.r") as mock_r:
            mock_r.get.return_value = None
            self.assertEqual(downloader.gather_input_dates(), [])

    def test_passes_through_datetime_strings_from_redis(self):
        downloader = Downloader()
        with patch("src.processors.download.r") as mock_r:
            mock_r.get.return_value = json.dumps(
                ["2026-05-08", "2026-06-01 07_00 PM"]
            ).encode()
            self.assertEqual(
                downloader.gather_input_dates(),
                ["2026-05-08", "2026-06-01 07_00 PM"],
            )


class TestDownloaderGetM3uUrl(unittest.TestCase):
    def test_extracts_chunklist_url_from_player_response(self):
        downloader = Downloader()
        mock_response = MagicMock()
        mock_response.text = PLAYER_HTML_WITH_M3U8
        mock_response.raise_for_status = MagicMock()
        with patch("src.processors.download.requests.get", return_value=mock_response):
            url = downloader.get_m3u_url("12345")
        self.assertEqual(
            url, "https://archive-stream.example.com/clip/12345/chunklist.m3u8"
        )

    def test_returns_empty_string_when_no_match(self):
        downloader = Downloader()
        mock_response = MagicMock()
        mock_response.text = "<html><body>nothing here</body></html>"
        mock_response.raise_for_status = MagicMock()
        with patch("src.processors.download.requests.get", return_value=mock_response):
            self.assertEqual(downloader.get_m3u_url("12345"), "")


if __name__ == "__main__":
    unittest.main()
