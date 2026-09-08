"""Async SQLAlchemy engine — SQLite (desktop) or PostgreSQL (contributor Docker)."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.log import get_logger
from app.core.paths import ensure_data_layout, sqlite_url

logger = get_logger("DatabaseService")

ensure_data_layout()

_backend = (settings.DATABASE_BACKEND or "sqlite").lower()
if _backend == "postgres" and settings.DATABASE_TRANSACTION_POOLER_URL:
    DATABASE_URL = settings.DATABASE_TRANSACTION_POOLER_URL
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
    logger.info("Using PostgreSQL database backend")
else:
    DATABASE_URL = sqlite_url()
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    logger.info("Using SQLite database at %s", DATABASE_URL)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

Base = declarative_base()


async def get_db():
    """FastAPI dependency that yields an async SQLAlchemy database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables if they do not exist (SQLite-friendly bootstrap)."""
    # Import models so metadata is populated (finance = Firefly, not local tables)
    import app.models.chat  # noqa: F401
    import app.models.kb  # noqa: F401
    import app.models.note  # noqa: F401
    import app.models.wikilink  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # create_all skips new indexes on existing SQLite tables — ensure key ones.
        def _ensure_sqlite_indexes(sync_conn) -> None:
            if sync_conn.dialect.name != "sqlite":
                return
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_notes_kb_rel_path ON notes (kb_id, rel_path)"
            )

        await conn.run_sync(_ensure_sqlite_indexes)
    logger.info("Database schema ensured (create_all)")
