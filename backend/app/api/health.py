"""Liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Lightweight liveness probe for the desktop supervisor (no KB/graph deps)."""
    return {"status": "healthy"}
