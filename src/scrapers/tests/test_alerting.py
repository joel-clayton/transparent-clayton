import unittest
from unittest import mock

from src.scrapers.alerting import AlertLevel, alert


class TestAlert(unittest.TestCase):
    def test_actionable_pages_discord(self):
        with mock.patch("src.scrapers.alerting.send_to_discord_bots") as send:
            alert(AlertLevel.ACTIONABLE, "boom")
        send.assert_called_once()
        self.assertIn("boom", send.call_args.args[0])

    def test_warning_does_not_page(self):
        with mock.patch("src.scrapers.alerting.send_to_discord_bots") as send:
            alert(AlertLevel.WARNING, "just noise")
        send.assert_not_called()

    def test_info_does_not_page(self):
        with mock.patch("src.scrapers.alerting.send_to_discord_bots") as send:
            alert(AlertLevel.INFO, "fyi")
        send.assert_not_called()

    def test_snapshot_ref_is_appended(self):
        with mock.patch("src.scrapers.alerting.send_to_discord_bots") as send:
            alert(
                AlertLevel.ACTIONABLE,
                "parser broke",
                snapshot_ref="42/2026-09-06T120000",
            )
        self.assertIn("42/2026-09-06T120000", send.call_args.args[0])

    def test_never_raises_when_channel_fails(self):
        with mock.patch(
            "src.scrapers.alerting.send_to_discord_bots",
            side_effect=RuntimeError("channel down"),
        ):
            alert(AlertLevel.ACTIONABLE, "boom")  # must not raise


if __name__ == "__main__":
    unittest.main()
