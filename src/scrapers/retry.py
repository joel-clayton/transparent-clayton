"""Retry with exponential backoff for transient scrape failures.

Only :class:`TransientScrapeError` is retried — anything else (notably
:class:`~src.scrapers.errors.SiteStructureError`) propagates immediately because
retrying a broken parser is pointless. A wall-clock ``deadline`` plus an
``attempts`` ceiling guarantee an unattended cron run can never loop forever.
"""

import logging
import random
import time
from typing import Callable, TypeVar

from src.scrapers.errors import TransientScrapeError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_transient(
    operation: Callable[[], T],
    *,
    label: str,
    attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    deadline: float | None = 120.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> T:
    """Run ``operation``, retrying transient failures with backoff + jitter.

    Gives up once ``attempts`` is exhausted or the next backoff would push total
    elapsed time past ``deadline`` seconds, re-raising as a
    :class:`TransientScrapeError` so the caller can alert on a sustained outage.
    ``sleep``/``monotonic`` are injectable for deterministic tests.
    """
    start = monotonic()
    last_error: TransientScrapeError | None = None
    attempt = 0
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except TransientScrapeError as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay *= 0.5 + random.random() * 0.5  # jitter: 50-100% of the delay
            elapsed = monotonic() - start
            if deadline is not None and elapsed + delay > deadline:
                logger.warning(
                    "%s: giving up after %.1fs (attempt %d/%d); next backoff "
                    "%.1fs would exceed the %.0fs deadline",
                    label,
                    elapsed,
                    attempt,
                    attempts,
                    delay,
                    deadline,
                )
                break
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                label,
                attempt,
                attempts,
                error,
                delay,
            )
            sleep(delay)
    raise TransientScrapeError(
        f"{label} failed after {attempt} attempt(s)"
    ) from last_error
