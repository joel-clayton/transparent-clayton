"""Failure-mode taxonomy for the meeting scraper.

The scraper has three distinguishable outcomes, so an unattended cron run can
tell "come look at this" apart from "just flaky, it'll pass next time":

* a normal empty result (no new meetings) — signalled by an empty return value,
  not an exception, matching the rest of the pipeline's "no missing dates" idiom;
* :class:`TransientScrapeError` — a network/timeout/WebDriver hiccup that is
  worth retrying and only alerts once the retry ceiling is hit;
* :class:`SiteStructureError` — the page loaded but the expected markup is gone,
  which retrying will not fix and which needs a human.
"""


class ScrapeError(Exception):
    """Base class for scraper failures."""


class TransientScrapeError(ScrapeError):
    """A transient failure (network, timeout, flaky WebDriver) worth retrying.

    Raised after the retry ceiling is exhausted so the caller can alert on a
    likely outage rather than on a broken parser.
    """


class SiteStructureError(ScrapeError):
    """The page loaded but the expected structure/selectors are absent.

    Retrying will not help — the source markup has probably changed and a human
    needs to fix the parser.
    """
