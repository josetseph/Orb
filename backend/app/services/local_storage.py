"""Local vault attachment storage (replaces RustFS for desktop)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from app.services.vault import save_attachment


def vault_rel_from_url(url: str) -> str | None:
    """Decoded vault-relative path of a link target, whichever form it is stored in.

    ``/vault-files/<any kb>/attachments/x%20y.pdf`` and the canonical
    ``attachments/x%20y.pdf`` both give ``attachments/x y.pdf``.
    """
    if not url:
        return None
    cleaned = url.replace("\\", "/")
    if cleaned.startswith("/vault-files/"):
        parts = cleaned.split("/", 3)  # '', vault-files, kb, rest
        return unquote(parts[3]) if len(parts) > 3 else None
    if cleaned.startswith("attachments/"):
        return unquote(cleaned)
    marker = "/attachments/"
    if marker in cleaned:
        return "attachments/" + unquote(cleaned.split(marker, 1)[1].split("?")[0])
    return None


def vault_file_url(link: str, kb_id: str) -> str:
    """Browser/serving URL for a canonical ``attachments/…`` link; other targets pass through."""
    return f"/vault-files/{kb_id}/{link}" if link.startswith("attachments/") else link


async def store_upload(vault: Path, filename: str, data: bytes, kb_id: str) -> dict:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "attachments").mkdir(parents=True, exist_ok=True)
    rel = save_attachment(vault, filename, data)
    return {"url": vault_file_url(rel, kb_id), "key": rel, "filename": filename}


async def remove_upload(vault: Path, key_or_url: str) -> None:
    from app.services.vault_ops import safe_vault_join

    rel = vault_rel_from_url(key_or_url) or key_or_url
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return
    try:
        path = safe_vault_join(Path(vault), rel)
    except ValueError:
        return
    if path.is_file():
        path.unlink()
