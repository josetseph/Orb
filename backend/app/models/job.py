"""Durable job rows — long work is written down before it starts (see app/services/jobs.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):  # pylint: disable=too-few-public-methods
    """One unit of long work: ``queued`` → ``running`` → ``done`` | ``failed`` | ``cancelled``."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_target", "kind", "kb_id", "target_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)  # also the drain order
    kind = Column(String, nullable=False)  # ingest_note, answer, attachment
    kb_id = Column(String, nullable=False)
    target_id = Column(String, nullable=False)  # note id, chat request id
    payload = Column(JSON, nullable=True)  # what the job needs that is not in another table
    input_hash = Column(String, nullable=False)  # what decides the result; a new one supersedes
    state = Column(String, nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)  # times started; a crash leaves it counted
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
