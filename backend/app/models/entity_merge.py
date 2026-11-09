"""One entity folded into another, with what it takes to undo it (app/services/entity_merge.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, String

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EntityMerge(Base):  # pylint: disable=too-few-public-methods
    """``queued`` → ``merged`` → ``undone``. ``plan`` is written once, before the first change."""

    __tablename__ = "entity_merges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String, nullable=False, index=True)
    keep_id = Column(String, nullable=False)
    absorbed_id = Column(String, nullable=False)
    state = Column(String, nullable=False, default="queued")
    plan = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
