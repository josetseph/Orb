"""Vault filesystem endpoints (move/delete/mkdir/list/resolve)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_kb
from app.core.database import get_db
from app.schemas.note import DeleteVaultFileInput, MkdirInput, MoveVaultFileInput
from app.services.kb_registry import KBContext

router = APIRouter()


@router.post("/api/v1/vault/move")
async def move_vault_path(
    body: MoveVaultFileInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Move any vault file (note or attachment) and rewrite markdown links."""
    from app.services.vault_ops import move_vault_file

    try:
        return await move_vault_file(db, kb, body.from_rel, body.to_rel)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/vault/delete")
async def delete_vault_path(
    body: DeleteVaultFileInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Delete a vault attachment and strip markdown links that pointed at it."""
    from app.services.vault_ops import delete_vault_file

    try:
        return await delete_vault_file(db, kb, body.rel_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/vault/mkdir")
async def mkdir_vault_folder(
    body: MkdirInput,
    kb: KBContext = Depends(get_kb),
):
    """Create an empty folder in the vault (for the notes sidebar tree)."""
    from app.services.vault_ops import safe_vault_join

    if not kb.vault_path:
        raise HTTPException(status_code=400, detail="No vault configured")
    rel = (body.path or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="Invalid folder path")
    try:
        vault = Path(kb.vault_path).expanduser().resolve()
        target = safe_vault_join(vault, rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes vault") from exc
    try:
        target.mkdir(parents=True, exist_ok=True)
        keep = target / ".keep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {exc}") from exc
    return {"path": rel, "status": "ok"}


@router.get("/api/v1/vault/folders")
async def list_folders(kb: KBContext = Depends(get_kb)):
    from app.services.vault_sync import list_attachment_files, list_vault_folders

    if not kb.vault_path:
        return {
            "folders": [],
            "attachments": [],
            "vault_name": "",
            "vault_path": "",
        }
    vault = Path(kb.vault_path).expanduser().resolve()

    def _list() -> dict:
        (vault / "attachments").mkdir(parents=True, exist_ok=True)
        return {
            "folders": list_vault_folders(vault, include_attachments=True),
            "attachments": list_attachment_files(vault),
            "vault_name": vault.name,
            "vault_path": str(vault),
        }

    return await asyncio.to_thread(_list)


@router.get("/api/v1/vault/local-path")
async def vault_local_path(
    rel: str = Query(..., description="Vault-relative path or /vault-files/... URL"),
    kb: KBContext = Depends(get_kb),
):
    """Resolve a vault-relative path (or vault-files URL) to an absolute local path."""
    from app.services.local_storage import vault_rel_from_url
    from app.services.vault_ops import safe_vault_join

    if not kb.vault_path:
        raise HTTPException(status_code=400, detail="No vault configured")
    raw = (rel or "").strip().replace("\\", "/")
    mapped = vault_rel_from_url(raw)
    if mapped:
        raw = mapped
    elif raw.startswith("vault-files/"):
        parts = raw.split("/", 2)
        raw = parts[2] if len(parts) > 2 else ""
    raw = raw.lstrip("/")
    if not raw or ".." in raw.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    vault = Path(kb.vault_path).expanduser().resolve()
    try:
        full = safe_vault_join(vault, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes vault") from exc
    if not full.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return {
        "rel_path": raw,
        "local_path": str(full),
        "vault_path": str(vault),
        "exists": True,
    }
