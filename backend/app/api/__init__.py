"""Register domain API routers on the FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import (
    admin,
    benchmark,
    chat,
    credentials,
    health,
    kb,
    models,
    notes,
)
from app.api_desktop import router as desktop_router


def register_all_routers(app: FastAPI) -> None:
    """Attach all Orb API routers (including local model setup)."""
    app.include_router(desktop_router)
    app.include_router(health.router)
    app.include_router(credentials.router)
    app.include_router(models.router)
    app.include_router(chat.router)
    app.include_router(notes.router)
    app.include_router(admin.router)
    app.include_router(benchmark.router)
    app.include_router(kb.router)
