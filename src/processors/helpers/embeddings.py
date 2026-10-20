"""Embedding helpers for the RAG index (TRA-176, increment 2).

All embedding goes through ``embed_document`` / ``embed_query`` so stored chunks and
queries always receive the embedding model's required task prefixes. Getting them
wrong — or mismatched between documents and queries — quietly degrades retrieval
(nomic-embed-text needs ``search_document:`` / ``search_query:``). The prefixes are
model-aware so swapping ``EMBEDDING_MODEL`` swaps them correctly.

Vectors from different models/versions must never share one index; a change of model
means rebuilding the store (see docs/design/phase-3b-rag-chunking.md).
"""

from __future__ import annotations

from src.processors.helpers.ollama_client import embed
from src.settings import EMBEDDING_MODEL

# (document_prefix, query_prefix) by model family, matched as a substring of the
# model name. Unknown models fall back to no prefix.
_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic": ("search_document: ", "search_query: "),
    # bge-*-en-v1.5: passages are unprefixed; queries get the retrieval instruction.
    "bge": ("", "Represent this sentence for searching relevant passages: "),
    # e5 / multilingual-e5.
    "e5": ("passage: ", "query: "),
}


def prefixes_for(model: str = EMBEDDING_MODEL) -> tuple[str, str]:
    """(document_prefix, query_prefix) for ``model`` — empty pair if unrecognized."""
    lowered = model.lower()
    for family, pair in _PREFIXES.items():
        if family in lowered:
            return pair
    return ("", "")


def embed_document(text: str, *, model: str = EMBEDDING_MODEL) -> list[float]:
    """Embed a stored chunk (applies the model's document prefix)."""
    return embed(model, prefixes_for(model)[0] + text)


def embed_query(text: str, *, model: str = EMBEDDING_MODEL) -> list[float]:
    """Embed a search query (applies the model's query prefix)."""
    return embed(model, prefixes_for(model)[1] + text)
