"""Central logging for Orb — component file routing under DATA_DIR/logs."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings

# Log formatters
file_formatter = logging.Formatter(
    fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

console_formatter = logging.Formatter(fmt="%(levelname)s: %(message)s")

# Mapping of logger names to filenames (under resolve_logs_dir()).
COMPONENT_LOG_FILES = {
    "API": "api.log",
    "KBRegistry": "api.log",
    "RuntimeConfig": "api.log",
    "ResetIndex": "api.log",
    "InitDB": "api.log",
    "uvicorn.access": "api.log",
    "uvicorn.error": "api.log",
    "IngestionPipeline": "ingestion.log",
    "IngestionTracker": "ingestion.log",
    "VaultSync": "ingestion.log",
    "VaultWatcher": "ingestion.log",
    "MultimediaService": "multimedia.log",
    "MultimodalRuntime": "multimedia.log",
    "MultimodalModels": "multimedia.log",
    "MultimodalServices": "multimedia.log",
    "ChatWorkflow": "chat.log",
    "ChatStore": "chat.log",
    "DatabaseService": "database.log",
    "GraphService": "graph.log",
    "LLMService": "llm.log",
    "LocalModels": "llm.log",
    "InferenceDevice": "llm.log",
    "RetrievalService": "retrieval.log",
    "QdrantService": "retrieval.log",
    "MeilisearchService": "retrieval.log",
    "RerankerService": "retrieval.log",
}

_configured = False


def resolve_logs_dir() -> Path:
    """Current logs directory from live ``settings.DATA_DIR`` (wizard-aware)."""
    root = Path(settings.DATA_DIR).expanduser() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


# Back-compat alias — prefer ``resolve_logs_dir()`` so path changes are picked up.
LOGS_DIR = resolve_logs_dir()


def get_file_handler(filename: str, level: int) -> RotatingFileHandler:
    """Create a rotating file handler under the current logs directory."""
    handler = RotatingFileHandler(
        resolve_logs_dir() / filename,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(file_formatter)
    return handler


def get_console_handler(level: int) -> logging.StreamHandler:
    """Create console handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(console_formatter)
    return handler


def _strip_rotating_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()


def setup_logging() -> None:
    """Initialize logging. Safe to call once at startup; use ``reconfigure_logging`` after path changes."""
    global _configured, LOGS_DIR  # noqa: PLW0603

    log_level_str = settings.LOG_LEVEL.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    LOGS_DIR = resolve_logs_dir()

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(get_console_handler(log_level))

    error_handler = get_file_handler("errors.log", logging.ERROR)
    root_logger.addHandler(error_handler)

    for logger_name, filename in COMPONENT_LOG_FILES.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)
        _strip_rotating_handlers(logger)
        # Drop duplicate console handlers if reconfiguring
        for handler in list(logger.handlers):
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, RotatingFileHandler
            ):
                logger.removeHandler(handler)
        logger.addHandler(get_file_handler(filename, log_level))
        logger.addHandler(get_console_handler(log_level))
        logger.addHandler(error_handler)
        logger.propagate = False

    for name in ("httpx", "httpcore", "asyncio", "urllib3", "multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    logging.info(
        "Logging initialized at level %s | Logs dir: %s", log_level_str, LOGS_DIR
    )


def reconfigure_logging() -> None:
    """Re-bind file handlers after DATA_DIR changes (first-run wizard)."""
    global LOGS_DIR  # noqa: PLW0603
    LOGS_DIR = resolve_logs_dir()
    if _configured:
        setup_logging()


def get_logger(name: str) -> logging.Logger:
    """Return a logger. Use a COMPONENT_LOG_FILES key for dedicated file routing."""
    return logging.getLogger(name)
