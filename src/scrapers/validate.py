"""URL reachability checks used to gate handoff to the Downloader.

A parsed record can be structurally valid yet point at a URL that 404s or has
already expired. :func:`url_resolves` sanity-checks a URL with a cheap HEAD (with
a ranged-GET fallback for CDNs that reject HEAD) so the scraper can quarantine a
dead-linked record instead of propagating garbage downstream.

A *network-level* failure is deliberately raised as
:class:`~src.scrapers.errors.TransientScrapeError` rather than reported as
"unreachable": during an outage every URL would look dead, and we would rather
retry/alert than silently quarantine every meeting.
"""

from typing import Any

import requests

from src.scrapers.constants import SCRAPE_URL_HEAD_TIMEOUT
from src.scrapers.errors import TransientScrapeError

# Statuses that usually mean "this server dislikes HEAD", not "the URL is dead";
# confirm those with a 1-byte ranged GET before deciding.
_HEAD_REJECT_STATUSES = frozenset({403, 405, 501})


def url_resolves(
    url: str,
    *,
    timeout: float = SCRAPE_URL_HEAD_TIMEOUT,
    session: Any = None,
) -> bool:
    """True if ``url`` resolves (final status < 400).

    Raises :class:`TransientScrapeError` on a network-level failure so the caller
    can retry/alert rather than treat a good URL as dead during an outage.
    """
    client = session or requests
    try:
        response = client.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code in _HEAD_REJECT_STATUSES:
            response = client.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                headers={"Range": "bytes=0-0"},
            )
            response.close()
        return response.status_code < 400
    except requests.RequestException as exc:
        raise TransientScrapeError(f"Could not reach {url}: {exc}") from exc


def url_resolves_best_effort(
    url: str,
    *,
    timeout: float = SCRAPE_URL_HEAD_TIMEOUT,
    session: Any = None,
) -> bool:
    """Like :func:`url_resolves` but swallows transient errors into ``False``.

    For non-blocking, warn-only checks (e.g. document URLs) where a failure
    should never abort the run.
    """
    try:
        return url_resolves(url, timeout=timeout, session=session)
    except TransientScrapeError:
        return False
