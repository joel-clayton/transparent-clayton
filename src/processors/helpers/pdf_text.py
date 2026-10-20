"""Extract text from a PDF for the summarizer (TRA-176).

Uses pdfplumber, which handles the multi-column layouts and data tables common in
city agenda packets better than a naive extractor. pdfplumber is imported lazily so
this module (and anything importing it) loads even where the dependency isn't
installed; the import error only surfaces when extraction is actually attempted.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pdf_to_text(path: str) -> str:
    """Plain text of a PDF, concatenated page by page. Returns ``""`` for an
    unreadable or empty PDF — a missing document must not abort a summary; the
    transcript and other inputs still stand."""
    import pdfplumber  # lazy: keeps the dependency optional at import time

    pages: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
    except Exception as exc:
        logger.warning("Could not extract text from %s: %s", path, exc)
        return ""
    return "\n\n".join(p for p in pages if p.strip()).strip()
