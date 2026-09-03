"""Register domain API routers on the FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import (
    admin,
    chat,
    credentials,
    files,
    graph,
    health,
    kb,
    notes,
    settings,
    vault,
)
from app.api_desktop import router as desktop_router


def register_all_routers(app: FastAPI) -> None:
    """Attach all Orb API routers (including desktop/finance)."""
    app.include_router(desktop_router)
    app.include_router(health.router)
    app.include_router(settings.router)
    app.include_router(credentials.router)
    app.include_router(files.router)
    app.include_router(chat.router)
    app.include_router(graph.router)
    app.include_router(notes.router)
    app.include_router(vault.router)
    app.include_router(admin.router)
    app.include_router(kb.router)
