"""Shared Google API auth that runs headless after a one-time consent.

The uploaders originally called ``InstalledAppFlow.run_local_server`` on every
run, which opens a browser each time — so the pipeline's upload steps were never
truly unattended. This centralises a cached-token flow: consent once, persist
the token, and silently refresh it thereafter. Interactive consent happens only
when there is no usable token (first run, or after the refresh token is revoked).

Each caller passes its own scopes, client-secret file, and token path, so the
existing per-service OAuth clients keep working; they just stop re-consenting.

Operational note: for the refresh token to survive beyond 7 days, the OAuth
consent screen must be published to "Production" (or "Internal" for Workspace)
in the Google Cloud console — a Testing-mode screen expires refresh tokens
weekly. That is a console setting, not something this code controls.
"""

import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)


def load_credentials(
    *,
    scopes: list[str],
    client_secret_path: str,
    token_path: str,
) -> Credentials:
    """Return valid Google credentials, avoiding interactive consent when possible.

    Loads a cached token and refreshes it silently; only falls back to the
    interactive consent flow when there is no usable token. Persists the
    (possibly refreshed) token so later runs need no browser.
    """
    creds: Credentials | None = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, scopes)
        except (ValueError, OSError) as exc:
            logger.warning("Ignoring unreadable token at %s: %s", token_path, exc)
            creds = None

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            logger.info("Refreshed Google credentials from %s", token_path)
        except Exception as exc:
            # A revoked/expired refresh token can't be refreshed — re-consent.
            logger.warning("Token refresh failed (%s); interactive consent needed", exc)
            creds = None

    if not creds or not creds.valid:
        logger.info("No usable cached token; starting interactive consent")
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, scopes)
        creds = flow.run_local_server(port=0)

    _persist(creds, token_path)
    return creds


def _persist(creds: Credentials, token_path: str) -> None:
    """Write the token so subsequent runs are headless. Best-effort."""
    try:
        parent = os.path.dirname(token_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(token_path, "w") as handle:
            handle.write(creds.to_json())
        os.chmod(token_path, 0o600)
    except OSError as exc:
        logger.warning("Could not persist token to %s: %s", token_path, exc)
