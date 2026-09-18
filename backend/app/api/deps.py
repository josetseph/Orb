"""Shared FastAPI dependencies for Orb API routers."""

from __future__ import annotations

from fastapi import HTTPException, Query

from app.services.kb_registry import KBContext, kb_registry


def get_kb(
    kb: str = Query(default="default", description="Knowledge base name or slug")
) -> KBContext:
    """Resolve the requested KB from the registry.

    Pass ``?kb=<name>`` in the query string. Omitting the parameter selects
    the default knowledge base (backward-compatible with existing clients).
    """
    ctx = kb_registry.get_kb_by_name(kb)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb}' not found")
    return ctx
