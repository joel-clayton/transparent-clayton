import os
import tempfile
import unittest
from unittest import mock

import src.processors.google_auth as ga


class TestLoadCredentials(unittest.TestCase):
    def _token(self, tmp, exists):
        path = os.path.join(tmp, "token.json")
        if exists:
            with open(path, "w") as handle:
                handle.write("{}")
        return path

    def test_valid_cached_token_skips_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = self._token(tmp, exists=True)
            creds = mock.Mock(valid=True)
            creds.to_json.return_value = "{}"
            with (
                mock.patch.object(ga, "Credentials") as Credentials,
                mock.patch.object(ga, "InstalledAppFlow") as Flow,
            ):
                Credentials.from_authorized_user_file.return_value = creds
                result = ga.load_credentials(
                    scopes=["s"], client_secret_path="cs", token_path=token
                )
            self.assertIs(result, creds)
            Flow.from_client_secrets_file.assert_not_called()

    def test_expired_token_is_refreshed_without_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = self._token(tmp, exists=True)
            creds = mock.Mock(valid=False, expired=True, refresh_token="r")
            creds.to_json.return_value = "{}"
            creds.refresh.side_effect = lambda _req: setattr(creds, "valid", True)
            with (
                mock.patch.object(ga, "Credentials") as Credentials,
                mock.patch.object(ga, "InstalledAppFlow") as Flow,
                mock.patch.object(ga, "Request"),
            ):
                Credentials.from_authorized_user_file.return_value = creds
                result = ga.load_credentials(
                    scopes=["s"], client_secret_path="cs", token_path=token
                )
            creds.refresh.assert_called_once()
            Flow.from_client_secrets_file.assert_not_called()
            self.assertIs(result, creds)

    def test_missing_token_triggers_consent_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = self._token(tmp, exists=False)
            new_creds = mock.Mock(valid=True)
            new_creds.to_json.return_value = "{}"
            flow = mock.Mock()
            flow.run_local_server.return_value = new_creds
            with (
                mock.patch.object(ga, "Credentials"),
                mock.patch.object(ga, "InstalledAppFlow") as Flow,
            ):
                Flow.from_client_secrets_file.return_value = flow
                result = ga.load_credentials(
                    scopes=["s"], client_secret_path="cs", token_path=token
                )
            self.assertIs(result, new_creds)
            Flow.from_client_secrets_file.assert_called_once()
            self.assertTrue(os.path.exists(token))  # token persisted for next run

    def test_unrefreshable_token_falls_back_to_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = self._token(tmp, exists=True)
            stale = mock.Mock(valid=False, expired=True, refresh_token="r")
            stale.refresh.side_effect = Exception("revoked")
            new_creds = mock.Mock(valid=True)
            new_creds.to_json.return_value = "{}"
            flow = mock.Mock()
            flow.run_local_server.return_value = new_creds
            with (
                mock.patch.object(ga, "Credentials") as Credentials,
                mock.patch.object(ga, "InstalledAppFlow") as Flow,
                mock.patch.object(ga, "Request"),
            ):
                Credentials.from_authorized_user_file.return_value = stale
                Flow.from_client_secrets_file.return_value = flow
                result = ga.load_credentials(
                    scopes=["s"], client_secret_path="cs", token_path=token
                )
            self.assertIs(result, new_creds)
            Flow.from_client_secrets_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
