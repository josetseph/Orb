"""Note metadata ORM — markdown body lives in the KB vault ``.md`` file."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, String

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Note(Base):  # pylint: disable=too-few-public-methods
    """Note metadata; body is at ``{vault_path}/{rel_path}``."""

    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_kb_rel_path", "kb_id", "rel_path"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Deprecated fallback only — ``persist_note_body`` keeps this empty.
    # Prefer vault file via ``rel_path``; see ``note_files.note_body``.
    title = Column(String, nullable=True)
    rel_path = Column(String, nullable=True)  # relative to KB vault_path
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )
    processed = Column(Boolean, default=False)
    failed = Column(Boolean, default=False)
    processing_stage = Column(String, nullable=True)
    processing_model = Column(String, nullable=True)
    kb_id = Column(String, nullable=False, default="default", index=True)
