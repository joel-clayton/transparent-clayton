"""Minimal Ollama client over HTTP (TRA-176).

Ollama runs a local server; we talk to it with ``requests`` (already a dependency)
rather than add the ``ollama`` package. Covers text generation (summaries), JSON-
constrained classification (segmentation), embeddings (RAG), and model metadata
(provenance digests).
"""

from __future__ import annotations

from typing import Any

import requests

from src.settings import OLLAMA_URL

# Local summarization over a long meeting can take minutes; allow for it.
DEFAULT_TIMEOUT = 600.0


def generate(
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    think: bool | None = None,
    options: dict[str, Any] | None = None,
    format: str | dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run a non-streaming completion and return the response text.

    ``system`` overrides/sets the system prompt (base models have none baked in).
    ``think=False`` suppresses the reasoning trace on thinking-capable models
    (qwen3), so classification/keyword calls return only the answer. ``options``
    passes Ollama options such as ``seed``/``temperature`` (seed + temperature 0
    make a call near-deterministic). ``format`` constrains output — ``"json"`` or a
    JSON schema dict — so a structured response parses reliably.
    """
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
    if system is not None:
        payload["system"] = system
    if think is not None:
        payload["think"] = think
    if options:
        payload["options"] = options
    if format is not None:
        payload["format"] = format
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
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


def show(model: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Model metadata from ``/api/show`` — used for provenance (the immutable
    ``digest`` of the model blob, not just its mutable tag)."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/show", json={"model": model}, timeout=timeout
    )
    resp.raise_for_status()
    return dict(resp.json())


def model_digest(model: str, *, timeout: float = 30.0) -> str | None:
    """The immutable blob digest for a model tag, from ``/api/tags``.

    Recorded in RAG provenance so a regeneration can tell whether the model changed
    even when the tag (``:latest``/``:14b``) was re-pulled to a different blob.
    Matches the tag with and without an implicit ``:latest``. Returns None if the
    model isn't found or Ollama is unreachable (provenance is best-effort).
    """
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models", [])
    except requests.RequestException:
        return None
    wanted = {model, model if ":" in model else f"{model}:latest"}
    for m in models:
        if m.get("name") in wanted or m.get("model") in wanted:
            digest = m.get("digest")
            return str(digest) if digest else None
    return None
