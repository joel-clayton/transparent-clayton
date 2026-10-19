"""Minimal Ollama client over HTTP (TRA-176).

Ollama runs a local server; we talk to it with ``requests`` (already a dependency)
rather than add the ``ollama`` package. Covers text generation (summaries) and, for
later RAG work, embeddings.
"""

from __future__ import annotations

from typing import Any

import requests

from src.settings import OLLAMA_URL

# Local summarization over a long meeting can take minutes; allow for it.
DEFAULT_TIMEOUT = 600.0


def generate(model: str, prompt: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run a non-streaming completion and return the response text."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return str(resp.json().get("response", ""))


def embed(model: str, text: str, *, timeout: float = DEFAULT_TIMEOUT) -> list[float]:
    """Return the embedding vector for ``text`` (for the Phase 3b RAG store)."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return list(data.get("embedding", []))
