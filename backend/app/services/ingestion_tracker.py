"""Ingestion tracking for idle-timeout community recomputation."""

import asyncio
import threading
from typing import Callable

from app.core.log import get_logger

logger = get_logger("IngestionTracker")

# Trigger Leiden recompute this many seconds after ALL ingestions have completed.
COMMUNITY_IDLE_SECONDS = 120  # 2 minutes


class IngestionTrackerService:
    """
    Tracks ingestions and triggers a full Leiden recompute
    ``COMMUNITY_IDLE_SECONDS`` (default 2 minutes) after the last ingestion finishes.
    If a new ingestion arrives while community detection is in progress, the running
    recompute is signalled to stop early so the system prioritises ingestion throughput.
    """

    def __init__(self):
        # Nodes touched since the last recompute — a UI counter only; Leiden rebuilds everything.
        self._pending_counts: dict[str, int] = {}
        self._community_recompute_running: dict[str, bool] = {}
        # Set when a run is interrupted mid-way so the next trigger doesn't skip
        # "No pending nodes" even though communities are only partially built.
        self._recompute_needed: dict[str, bool] = {}
        # Tracks how many ingestion pipelines are currently running per KB.
        self._active_ingestion_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        # Threading event used to signal a running rebuild to stop between clusters.
        self.cancel_recompute: threading.Event = threading.Event()
        # Threading event used to signal a running temporal digest build to stop.
        self.cancel_temporal: threading.Event = threading.Event()

    def has_active_ingestions(self, kb_id: str | None = None) -> bool:
        """Thread-safe enough read helper for worker threads."""
        if kb_id is not None:
            return self._active_ingestion_counts.get(kb_id, 0) > 0
        return any(c > 0 for c in self._active_ingestion_counts.values())

    def get_status_snapshot(self, kb_id: str = "default") -> dict:
        """Lightweight status for the global sidebar indicator."""
        active = self._active_ingestion_counts.get(kb_id, 0)
        pending = self._pending_counts.get(kb_id, 0)
        running = self._community_recompute_running.get(kb_id, False)
        needed = self._recompute_needed.get(kb_id, False)
        task = self._debounce_tasks.get(kb_id)
        return {
            "active_ingestions": active,
            "pending_community_nodes": pending,
            "community_recompute_running": running,
            "community_recompute_needed": needed,
            "community_idle_seconds": COMMUNITY_IDLE_SECONDS,
            "community_timer_armed": bool(task and not task.done()),
        }

    async def begin_ingestion(self, kb_id: str = "default") -> None:
        """
        Call at the START of every ingestion pipeline.
        Increments the active-ingestion counter and cancels any pending
        community-recompute timer so it can never fire while work is in progress.
        """
        async with self._lock:
            current_active = self._active_ingestion_counts.get(kb_id, 0) + 1
            self._active_ingestion_counts[kb_id] = current_active
            active = current_active
            task = self._debounce_tasks.get(kb_id)
            if task and not task.done():
                task.cancel()
                self._debounce_tasks.pop(kb_id, None)

            # Immediate preemption signal: request cancellation as soon as ingestion starts
            # so any in-flight recompute path can stop cooperatively.
            self.cancel_recompute.set()
            self.cancel_temporal.set()

            recompute_running = self._community_recompute_running.get(kb_id, False)
        logger.info(
            f"[IngestionTracker][{kb_id}] Ingestion started — active: {active}, community timer cancelled"
        )
        if recompute_running:
            logger.info(
                f"[IngestionTracker][{kb_id}] Ingestion started while recompute is running — "
                "signalling immediate cancellation."
            )

    async def end_ingestion(self, callback: Callable, kb_id: str = "default") -> None:
        """
        Call at the END of every ingestion pipeline (success or failure).
        Decrements the active counter; when it reaches 0 and community auto-detect
        is enabled, starts the idle timer for Leiden recompute.
        """
        from app.core.config import settings as _settings

        async with self._lock:
            current = self._active_ingestion_counts.get(kb_id, 1)
            self._active_ingestion_counts[kb_id] = max(0, current - 1)
            active = self._active_ingestion_counts[kb_id]
        logger.info(f"[IngestionTracker][{kb_id}] Ingestion ended — active: {active}")
        if active == 0 and not self._community_recompute_running.get(kb_id, False):
            logger.info(
                f"[IngestionTracker][{kb_id}] All ingestions complete — starting "
                f"{COMMUNITY_IDLE_SECONDS}s idle timer for community recompute"
            )
            self.schedule_recompute(callback, kb_id=kb_id)

    async def queue_nodes_for_community_recompute(
        self, node_count: int, kb_id: str = "default"
    ) -> int:
        """
        Mark a recompute as needed after an ingestion touched ``node_count`` nodes.
        If a recompute is already running, signal it to cancel so ingestion takes priority.
        Returns the running count of touched nodes (shown in the UI).
        """
        async with self._lock:
            queue_size = self._pending_counts.get(kb_id, 0) + node_count
            self._pending_counts[kb_id] = queue_size
            self._recompute_needed[kb_id] = True
            if self._community_recompute_running.get(kb_id, False):
                logger.info(
                    f"[IngestionTracker][{kb_id}] Ingestion arrived while recompute is running — "
                    "signalling recompute to cancel (will restart after next idle period)."
                )
                self.cancel_recompute.set()
        logger.info(
            f"[IngestionTracker][{kb_id}] Queued {node_count} nodes "
            f"(total pending: {queue_size})"
        )
        return queue_size

    def schedule_recompute(self, callback: Callable, kb_id: str = "default") -> None:
        """Debounce: cancel any existing timer and start a fresh COMMUNITY_IDLE_SECONDS countdown."""
        task = self._debounce_tasks.get(kb_id)
        if task and not task.done():
            task.cancel()
            logger.info(
                f"[IngestionTracker][{kb_id}] Recompute timer reset "
                f"({COMMUNITY_IDLE_SECONDS}s idle trigger)"
            )
        else:
            logger.info(
                f"[IngestionTracker][{kb_id}] Recompute timer started "
                f"({COMMUNITY_IDLE_SECONDS}s idle trigger)"
            )
        try:
            loop = asyncio.get_running_loop()
            self._debounce_tasks[kb_id] = loop.create_task(
                self._debounce_recompute(callback, kb_id=kb_id)
            )
        except RuntimeError:
            logger.warning(
                f"[IngestionTracker][{kb_id}] No running event loop; recompute timer not started."
            )

    async def _debounce_recompute(
        self, callback: Callable, kb_id: str = "default"
    ) -> None:
        try:
            await asyncio.sleep(COMMUNITY_IDLE_SECONDS)
        except asyncio.CancelledError:
            return

        async with self._lock:
            if self._community_recompute_running.get(kb_id, False):
                logger.info(
                    f"[IngestionTracker][{kb_id}] Recompute already running; skipping debounce trigger."
                )
                return
            queue_size = self._pending_counts.get(kb_id, 0)
            if not queue_size and not self._recompute_needed.get(kb_id, False):
                logger.info(
                    f"[IngestionTracker][{kb_id}] No pending nodes after idle wait; skipping recompute."
                )
                return
            self._community_recompute_running[kb_id] = True
            self._recompute_needed[kb_id] = False
            self._pending_counts[kb_id] = 0

        # Clear any previous cancellation signal before starting the new run.
        self.cancel_recompute.clear()

        logger.info(
            f"[IngestionTracker][{kb_id}] Community recompute triggered after "
            f"{COMMUNITY_IDLE_SECONDS}s idle: {queue_size} pending nodes"
        )
        cancelled_early = False
        try:
            await asyncio.to_thread(callback)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                f"[IngestionTracker][{kb_id}] Leiden recompute failed: {e}", exc_info=True
            )
        else:
            cancelled_early = self.cancel_recompute.is_set()
        finally:
            await self.mark_community_recompute_complete(kb_id=kb_id)

        if cancelled_early:
            # Mark that communities are only partially built so the next debounce
            # trigger doesn't skip the "no pending nodes" gate.
            async with self._lock:
                self._recompute_needed[kb_id] = True
                should_reschedule = self._active_ingestion_counts.get(kb_id, 0) == 0
            if should_reschedule:
                logger.info(
                    f"[IngestionTracker][{kb_id}] Recompute exited early; no active ingestions — "
                    f"rescheduling full rebuild in {COMMUNITY_IDLE_SECONDS}s."
                )
                self.schedule_recompute(callback, kb_id=kb_id)
            else:
                logger.info(
                    f"[IngestionTracker][{kb_id}] Recompute exited early; ingestion still active — "
                    "timer will start when last ingestion ends."
                )

    async def mark_community_recompute_complete(self, kb_id: str = "default"):
        """Mark the background community-recompute pass as finished."""
        async with self._lock:
            self._community_recompute_running[kb_id] = False

# Global singleton
ingestion_tracker = IngestionTrackerService()
