"""Shared FastAPI dependencies for Orb API routers."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query

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


def get_finance_kb(kb: KBContext = Depends(get_kb)) -> KBContext:
    """Resolve the KB, refusing if finance is switched off for it.

    Guards the write and read routes so a stale tab cannot post transactions
    into a KB whose finance section the user has turned off. The workspace
    endpoint deliberately does not use this — it reports the off state instead,
    which is what the UI renders.
    """
    if not kb.finance_enabled:
        raise HTTPException(
            status_code=403,
            detail=f"Finance is turned off for the '{kb.name}' knowledge base.",
        )
    return kb
