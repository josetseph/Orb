"""FastAPI application entry point: middleware, startup hooks, router wiring."""

# pylint: disable=wrong-import-order,wrong-import-position,import-outside-toplevel
import asyncio
import uuid
from contextvars import ContextVar
from pathlib import Path

# Setup logging before any other imports — must precede service imports so
# every module that calls get_logger() at import time finds logging configured.
from app.core.log import get_logger, setup_logging

setup_logging()

from app.api import register_all_routers  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import init_db  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.exceptions import HTTPException  # noqa: E402

logger = get_logger("API")

# Stores the current request's trace_id for the duration of a request.
# Use `request_trace_id.get()` in any async context to retrieve it.
request_trace_id: ContextVar[str] = ContextVar("request_trace_id", default="")

app = FastAPI(title="Orb API", version="1.0.0")
register_all_routers(app)

cors_origins = [
    origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """Attach a trace_id to every inbound request.

    The trace_id is:
      1. Read from the incoming X-Request-Id header if provided by the caller.
      2. Generated as a new UUID4 otherwise.

    The value is stored in a ContextVar so any logger that reads it can attach
    it to structured log records without explicit passing. It is also returned
    in the X-Request-Id response header so callers can correlate server-side
    logs with their own traces.
    """
    trace_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    token = request_trace_id.set(trace_id)
    try:
        response: Response = await call_next(request)
    finally:
        request_trace_id.reset(token)
    response.headers["X-Request-Id"] = trace_id
    return response


@app.on_event("startup")
async def startup_event():
    """Initialize external services and database tables on application startup."""
    logger.info("Application startup: Orb API online")
    await init_db()
    from app.core import runtime_config

    overrides = runtime_config.load()
    if overrides:
        runtime_config.apply_to_settings(overrides)
        logger.info(
            "Runtime config overrides applied",
            extra={"overrides": list(overrides.keys())},
        )
    try:
        # Nothing is ingesting at boot, so a note still mid-pipeline was
        # interrupted (crash, quit, restart). Left as-is the UI treats it as
        # running forever and refuses to re-ingest it.
        from sqlalchemy import or_, update

        from app.core.database import AsyncSessionLocal
        from app.models.note import Note

        idle_stages = ("Saved", "Ingestion complete", "Ingestion failed")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(Note)
                .where(
                    Note.processed.is_(False),
                    Note.failed.is_(False),
                    Note.processing_stage.is_not(None),
                    Note.processing_stage.not_in(idle_stages),
                    or_(
                        Note.processing_stage.not_like("%pending%"),
                        Note.processing_stage.is_(None),
                    ),
                    Note.processing_stage.not_like("Changed on disk%"),
                )
                .values(failed=True, processing_stage="Ingestion failed (interrupted)")
            )
            await session.commit()
            if result.rowcount:
                logger.warning(
                    "Reset %d note(s) left mid-ingest by a restart", result.rowcount
                )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(f"Could not reset interrupted ingests: {exc}")

    # Neither blocks serving: the desktop window opens as soon as /health answers.
    asyncio.get_running_loop().run_in_executor(None, _background_startup)


def _background_startup() -> None:
    try:
        from app.services.local_models import sync_embedding_infrastructure

        sync_embedding_infrastructure()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(f"Embedding infrastructure sync skipped: {exc}")
    try:
        from app.services.vault_watcher import start_vault_watchers

        start_vault_watchers()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(f"Vault watcher not started: {exc}")
    _migrate_stores()


def _migrate_stores() -> None:
    """One-time per-KB scrubs: legacy ``FACTS:`` descriptions in Qdrant and
    unnamed note nodes in Kuzu. Gated by a marker file per KB, written only
    once both stores answered."""
    import sqlite3

    from app.core.paths import resolve_data_dir
    from app.services.kb_registry import kb_registry

    data = resolve_data_dir()
    for meta in kb_registry.list_kbs():
        kb_id = meta["id"]
        marker = data / f".stores-migrated-v1-{kb_id}"
        if marker.exists():
            continue
        try:
            kb = kb_registry.get_kb(kb_id)
            # Predicate default renamed relates_to -> related_to (closed vocabulary).
            kb.graph.execute_query(
                "MATCH ()-[r:SEMANTIC_REL {rel_type: 'relates_to'}]->() SET r.rel_type = 'related_to'"
            )
            scrubbed = kb.qdrant.strip_facts_prefixes()
            unnamed = kb.graph.execute_query(
                "MATCH (n:Node {kind: 'note'}) "
                "WHERE n.name IS NULL OR n.name IN ['', 'Unknown', 'Untitled'] "
                "RETURN n.id AS id"
            )
            named = 0
            if unnamed:
                conn = sqlite3.connect(data / "orb.db")
                titles = {
                    nid: (title or "").strip() or Path(rel or "").stem
                    for nid, title, rel in conn.execute(
                        "SELECT id, title, rel_path FROM notes WHERE kb_id = ?", (kb_id,)
                    )
                }
                conn.close()
                for row in unnamed:
                    if titles.get(row["id"]):
                        kb.graph.execute_query(
                            "MATCH (n:Node {id: $id}) SET n.name = $name",
                            {"id": row["id"], "name": titles[row["id"]]},
                        )
                        named += 1
            marker.touch()
            logger.info(
                "Store migration v1 for KB '%s': %d descriptions scrubbed, %d note names filled",
                meta.get("name"), scrubbed, named,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Store migration v1 for KB '%s' deferred to next start: %s", meta.get("name"), exc
            )


@app.on_event("shutdown")
async def shutdown_event():
    try:
        from app.services.vault_watcher import stop_vault_watchers

        stop_vault_watchers()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


class _SpaFiles(StaticFiles):
    """Static UI with history-API fallback: /notes, /chat… all load index.html."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.split("/", 1)[0] in ("api", "vault-files"):
                raise
            return await super().get_response("index.html", scope)


# The desktop app has no UI server: the API serves the Vite build so the window
# and the API share one origin. Mounted last so every real route wins.
_frontend_dir = Path(
    settings.FRONTEND_DIR or Path(__file__).resolve().parents[2] / "frontend" / "dist"
)
if (_frontend_dir / "index.html").is_file():
    app.mount("/", _SpaFiles(directory=_frontend_dir, html=True), name="ui")
    logger.info(f"Serving UI from {_frontend_dir}")
