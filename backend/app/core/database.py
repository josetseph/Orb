"""Async SQLAlchemy engine over the desktop SQLite database."""

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

DATABASE_URL = sqlite_url()
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
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


# create_all skips new indexes on existing SQLite tables — ensure key ones.
def _sqlite_repairs(sync_conn) -> None:
    if sync_conn.dialect.name != "sqlite":
        return
    sync_conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_notes_kb_rel_path ON notes (kb_id, rel_path)"
    )
    # rel_path was once str(Path) — backslashes on Windows.
    sync_conn.exec_driver_sql(
        "UPDATE notes SET rel_path = replace(rel_path, '\\', '/') "
        "WHERE rel_path LIKE '%\\%'"
    )
    # Bodies moved to vault files; drop the column once nothing is left in it.
    cols = {r[1] for r in sync_conn.exec_driver_sql("PRAGMA table_info(notes)")}
    if "content" in cols:
        left = sync_conn.exec_driver_sql(
            "SELECT count(*) FROM notes WHERE content IS NOT NULL AND content != ''"
        ).scalar()
        if left:
            logger.warning("notes.content still holds %d bodies; column kept", left)
        else:
            sync_conn.exec_driver_sql("ALTER TABLE notes DROP COLUMN content")


async def init_db() -> None:
    """Create tables if they do not exist (SQLite-friendly bootstrap)."""
    # Import models so metadata is populated (finance = Firefly, not local tables)
    import app.models.chat  # noqa: F401
    import app.models.note  # noqa: F401
    import app.models.wikilink  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        await conn.run_sync(_sqlite_repairs)
    logger.info("Database schema ensured (create_all)")
