"""Pluggable output destinations for the pipeline (Phase 3)."""

from src.publishers.base import Publisher, is_destination_enabled
from src.publishers.registry import PUBLISHERS, run_publisher

__all__ = [
    "Publisher",
    "is_destination_enabled",
    "PUBLISHERS",
    "run_publisher",
]
