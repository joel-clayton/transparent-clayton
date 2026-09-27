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


class TestDownloaderProcessContinuesOnError(unittest.TestCase):
    DATES = ["2026-05-08", "2026-05-15", "2026-05-22"]

    def _run(self, failing):
        downloader = Downloader()
        attempted = []

        def download_one(date, outfile):
            attempted.append(date)
            if date in failing:
                raise RuntimeError(f"boom {date}")

        with (
            patch.object(
                downloader, "get_most_recent_missing_dates", return_value=self.DATES
            ),
            patch.object(downloader, "_download_one", side_effect=download_one),
            patch.object(
                downloader,
                "construct_filepath_for_date",
                side_effect=lambda d: f"/nope/{d}.mp4",
            ),
            patch("src.processors.download.os.path.exists", return_value=False),
            patch("src.processors.download.r") as mock_r,
            patch("src.processors.download.alert") as mock_alert,
        ):
            downloader.process()
        marked = [call.args[1] for call in mock_r.hset.call_args_list]
        return attempted, marked, mock_alert

    def test_one_failure_does_not_block_the_rest(self):
        attempted, marked, mock_alert = self._run(failing={"2026-05-15"})
        self.assertEqual(attempted, self.DATES)  # all attempted
        self.assertEqual(marked, ["2026-05-08", "2026-05-22"])  # only successes marked
        mock_alert.assert_called_once()
        self.assertIn("2026-05-15", mock_alert.call_args.args[1])

    def test_no_alert_when_all_succeed(self):
        attempted, marked, mock_alert = self._run(failing=set())
        self.assertEqual(marked, self.DATES)
        mock_alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
