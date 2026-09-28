"""A common seam for the pipeline's output destinations (Phase 3, TRA-132).

The workflow used to call each publishing processor directly and in a fixed
order. ``Publisher`` wraps those processors behind one interface so the workflow
can dispatch destinations uniformly, skip disabled ones (TRA-133), and let the
reconciliation script query each destination the same way (TRA-137/138/143).

This module introduces the abstraction only — it does not change what runs. Every
destination defaults to enabled, so the pipeline behaves exactly as before until
a destination is explicitly turned off.
"""

from abc import ABC, abstractmethod

from src import settings
from src.meeting_types import MeetingType


def is_destination_enabled(destination: str) -> bool:
    """Whether a publishing destination is turned on.

    Defaults to enabled; a destination named in ``DISABLED_PUBLISHERS`` (settings)
    is skipped. TRA-133 will grow this into a fuller per-destination config
    surface — for now it is the single switch the registry consults.
    """
    return destination not in settings.DISABLED_PUBLISHERS


class Publisher(ABC):
    """One publishing stage that sends processed meeting assets to a destination.

    Subclasses wrap an existing processor (or processors) so behavior is
    unchanged; they only add a uniform ``run``/``is_enabled`` surface and a place
    to hang reconciliation later.
    """

    #: Stage key used by the workflow and the registry (e.g. "upload_video").
    name: str
    #: Config grouping for enable/disable (e.g. "youtube", "google_docs", "wiki").
    #: Several stages may share a destination (transcripts + documents = Drive).
    destination: str

    def is_enabled(self) -> bool:
        return is_destination_enabled(self.destination)

    @abstractmethod
    def run(self) -> None:
        """Execute this stage across all meeting types, idempotently."""

    def reconcile_state(
        self, meeting_type: MeetingType, meeting_key: str
    ) -> object | None:
        """What this destination holds for a meeting, for the reconciliation
        script to query uniformly.

        Optional extension point: ``None`` means this publisher does not yet
        participate in reconciliation. Concrete publishers gain real
        implementations as reconcile grows to cover each destination
        (TRA-137/138/143).
        """
        return None
