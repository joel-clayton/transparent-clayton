"""A single alerting seam that routes by severity.

Unattended runs need to distinguish noise from "come look at this": normal
events and expected per-record skips should stay in the logs, while a broken
parser or a sustained outage should page a human. ``alert`` centralises that
routing so callers just pick a level, and it never raises — a failure in the
alert channel must not take down the pipeline that is trying to report a problem.

Keeping this behind one function also makes the notification channel swappable
later (Phase 3+) without touching every call site.
"""

import logging
from enum import Enum

from src.util import send_to_discord_bots

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"  # routine; logged only
    WARNING = "warning"  # notable but not urgent (e.g. batched quarantines); logged
    ACTIONABLE = "actionable"  # a human needs to act; logged and paged to Discord


def alert(level: AlertLevel, message: str, *, snapshot_ref: str | None = None) -> None:
    """Route an alert by severity. Never raises.

    ``snapshot_ref``, when given, is appended so a broken-parser page points at
    the raw capture to replay.
    """
    text = f"{message} (snapshot: {snapshot_ref})" if snapshot_ref else message

    if level is AlertLevel.ACTIONABLE:
        logger.error(text)
        try:
            send_to_discord_bots(text)
        except Exception as exc:
            # Alerting is best-effort; a dead channel must not crash the caller.
            logger.warning("Alert delivery failed (%s): %s", type(exc).__name__, exc)
    elif level is AlertLevel.WARNING:
        logger.warning(text)
    else:
        logger.info(text)
