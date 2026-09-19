"""Watch vault folders for external .md changes and refresh note metadata.

Does NOT auto-ingest. Marks previously ingested notes as stale so the user can
re-ingest manually. Ignores brief self-writes from Orb saves.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.core.log import get_logger

logger = get_logger("VaultWatcher")

_watcher_thread: threading.Thread | None = None
_stop = threading.Event()
_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """Shared sync engine — a OneDrive sync burst of hundreds of file events
    must not create one engine per event."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from sqlalchemy import create_engine

                from app.core.paths import sqlite_url

                _engine = create_engine(sqlite_url(driver="pysqlite"), future=True)
    return _engine


def _sync_vault_file(kb_id: str, vault: Path, rel: str, event: str) -> None:
    """Best-effort sync of a single vault file into SQLite metadata."""
    from datetime import datetime, timezone

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.note import Note
    from app.services.vault import is_recent_self_write, read_note_file, title_from_filename

    if event != "deleted" and is_recent_self_write(vault / rel):
        logger.debug(f"[VaultWatcher] ignore self-write {kb_id}:{rel}")
        return

    with Session(_get_engine()) as session:
        if event == "deleted":
            row = session.execute(
                select(Note).where(Note.kb_id == kb_id, Note.rel_path == rel)
            ).scalar_one_or_none()
            if row:
                row.processing_stage = "External delete detected — review in Orb"
                session.commit()
            return

        body = read_note_file(vault, rel) if (vault / rel).exists() else ""
        title = title_from_filename(rel)
        row = session.execute(
            select(Note).where(Note.kb_id == kb_id, Note.rel_path == rel)
        ).scalar_one_or_none()
        if row is None:
            # New file on disk — index metadata only; user must click Ingest.
            row = Note(
                kb_id=kb_id,
                title=title,
                rel_path=rel,
                content="",
                processed=False,
                processing_stage="Saved",
            )
            session.add(row)
            session.flush()
        else:
            # Keep user-set display titles; filename is only a fallback
            if not (row.title or "").strip():
                row.title = title
            row.updated_at = datetime.now(timezone.utc)
            # Never queue ingestion. Autosave / typing must not flip ingest UI.
            # Only mark previously ingested notes as stale so the user can
            # click Ingest again when ready.
            if row.processed:
                row.processed = False
                row.processing_stage = "Changed on disk — re-ingest when ready"
            # Unprocessed notes stay as Saved (or leave an active ingest stage alone).
            elif row.processing_stage and (
                row.processing_stage.startswith("Queued")
                or row.processing_stage.startswith("Starting")
            ):
                pass
            else:
                row.processing_stage = "Saved"

        # Keep wikilink graph in sync even when body stays vault-backed (DB content empty).
        from app.services.wikilinks import refresh_note_links_sync

        refresh_note_links_sync(session, kb_id, row.id, body)
        session.commit()


def start_vault_watchers() -> None:
    """Start background watchdog threads for all registered KB vaults."""
    global _watcher_thread  # noqa: PLW0603
    if _watcher_thread and _watcher_thread.is_alive():
        return
    _stop.clear()

    def _run() -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("watchdog not installed — vault watcher disabled")
            return

        from app.services.kb_registry import kb_registry

        class VaultHandler(FileSystemEventHandler):
            def __init__(self, kb_id: str, vault: Path):
                super().__init__()
                self.kb_id = kb_id
                self.vault = vault
                self._debounce: dict[str, float] = {}

            def _rel(self, path: str) -> str | None:
                try:
                    rel = str(Path(path).resolve().relative_to(self.vault.resolve()))
                except ValueError:
                    return None
                if not rel.endswith(".md"):
                    return None
                if rel.startswith("attachments/") or "/." in f"/{rel}":
                    return None
                return rel.replace("\\", "/")

            def _queue(self, path: str, event: str) -> None:
                rel = self._rel(path)
                if not rel:
                    return
                now = time.time()
                key = f"{rel}:{event}"
                if now - self._debounce.get(key, 0) < 0.8:
                    return
                self._debounce[key] = now
                try:
                    _sync_vault_file(self.kb_id, self.vault, rel, event)
                    logger.info(f"[VaultWatcher] {event} {self.kb_id}:{rel}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(f"[VaultWatcher] sync failed: {exc}")

            def on_created(self, event):
                if not event.is_directory:
                    self._queue(event.src_path, "created")

            def on_modified(self, event):
                if not event.is_directory:
                    self._queue(event.src_path, "modified")

            def on_deleted(self, event):
                if not event.is_directory:
                    self._queue(event.src_path, "deleted")

            def on_moved(self, event):
                if not event.is_directory:
                    self._queue(getattr(event, "dest_path", event.src_path), "modified")
                    self._queue(event.src_path, "deleted")

        observer = Observer()
        watched: set[str] = set()

        def refresh_observers() -> None:
            for meta in kb_registry.list_kbs():
                vault = meta.get("vault_path")
                kb_id = meta.get("id") or "default"
                if not vault or vault in watched:
                    continue
                p = Path(vault)
                if not p.exists():
                    continue
                observer.schedule(VaultHandler(kb_id, p), str(p), recursive=True)
                watched.add(vault)
                logger.info(f"[VaultWatcher] watching {vault}")

        refresh_observers()
        observer.start()
        try:
            while not _stop.is_set():
                refresh_observers()
                _stop.wait(30)
        finally:
            observer.stop()
            observer.join(timeout=5)

    _watcher_thread = threading.Thread(target=_run, name="vault-watcher", daemon=True)
    _watcher_thread.start()
    logger.info("Vault watcher thread started")


def stop_vault_watchers() -> None:
    _stop.set()
