"""``created_at`` on note inputs is a datetime: garbage is a 422, naive is UTC."""

from datetime import timezone

import pytest
from pydantic import ValidationError

from app.schemas.note import CreateNoteInput


def test_garbage_created_at_is_rejected():
    with pytest.raises(ValidationError):
        CreateNoteInput(created_at="not a date")


def test_iso_z_created_at_is_aware_utc():
    dt = CreateNoteInput(created_at="2026-09-19T10:00:00.000Z").created_at
    assert dt is not None and dt.utcoffset().total_seconds() == 0


def test_naive_created_at_defaults_to_utc():
    from app.api.notes import _aware

    dt = _aware(CreateNoteInput(created_at="2026-09-19T10:00:00").created_at)
    assert dt.tzinfo is timezone.utc


def test_endpoint_returns_422_for_garbage_date():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import notes
    from app.api.deps import get_kb
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(notes.router)
    app.dependency_overrides[get_kb] = lambda: None
    app.dependency_overrides[get_db] = lambda: None
    res = TestClient(app).post("/api/v1/notes", json={"content": "", "created_at": "garbage"})
    assert res.status_code == 422
