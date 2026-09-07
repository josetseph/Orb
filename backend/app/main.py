"""FastAPI application entry point: middleware, startup hooks, router wiring."""

# pylint: disable=wrong-import-order,wrong-import-position,import-outside-toplevel
import uuid
from contextvars import ContextVar

# Setup logging before any other imports — must precede service imports so
# every module that calls get_logger() at import time finds logging configured.
from app.core.log import get_logger, setup_logging

setup_logging()

from app.api import register_all_routers  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import init_db  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

logger = get_logger("API")

# Stores the current request's trace_id for the duration of a request.
# Use `request_trace_id.get()` in any async context to retrieve it.
request_trace_id: ContextVar[str] = ContextVar("request_trace_id", default="")

app = FastAPI(title="Orb API", version="0.1.0")
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


@app.on_event("shutdown")
async def shutdown_event():
    try:
        from app.services.vault_watcher import stop_vault_watchers

        stop_vault_watchers()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
