"""Bookkeeping of in-flight ingestions.

Community detection and temporal digests run only when asked (the Setup page
button → the admin endpoints); a new ingestion signals a running rebuild to
stop between clusters / buckets so ingestion keeps priority.
"""

import asyncio
import threading

from app.core.log import get_logger

logger = get_logger("IngestionTracker")


class IngestionTrackerService:
    """Per-KB count of running ingestion pipelines plus the cancel signals."""

    def __init__(self):
        self._active_ingestion_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        # Set when an ingestion starts; a running rebuild checks it between steps.
        self.cancel_recompute: threading.Event = threading.Event()
        self.cancel_temporal: threading.Event = threading.Event()

    def active_count(self, kb_id: str) -> int:
        return self._active_ingestion_counts.get(kb_id, 0)

    def has_active_ingestions(self, kb_id: str | None = None) -> bool:
        """Thread-safe enough read helper for worker threads."""
        if kb_id is not None:
            return self.active_count(kb_id) > 0
        return any(c > 0 for c in self._active_ingestion_counts.values())

    async def begin_ingestion(self, kb_id: str = "default") -> None:
        """Call at the START of every ingestion pipeline."""
        async with self._lock:
            active = self._active_ingestion_counts.get(kb_id, 0) + 1
            self._active_ingestion_counts[kb_id] = active
            self.cancel_recompute.set()
            self.cancel_temporal.set()
        logger.info(f"[IngestionTracker][{kb_id}] Ingestion started — active: {active}")

    async def end_ingestion(self, kb_id: str = "default") -> None:
        """Call at the END of every ingestion pipeline (success or failure)."""
        async with self._lock:
            current = self._active_ingestion_counts.get(kb_id, 1)
            self._active_ingestion_counts[kb_id] = max(0, current - 1)
            active = self._active_ingestion_counts[kb_id]
        logger.info(f"[IngestionTracker][{kb_id}] Ingestion ended — active: {active}")


# Global singleton
ingestion_tracker = IngestionTrackerService()
