"""Reconcile SQLite note rows with markdown files on disk (folder paths included)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import get_logger
from app.models.note import Note
from app.services.kb_registry import KBContext
from app.services.vault import title_from_filename

logger = get_logger("VaultSync")


def _iter_rel(vault: Path, keep) -> Iterator[tuple[Path, str]]:
    """Yield ``(path, vault-relative posix path)`` for non-hidden entries ``keep`` accepts."""
    if not vault.exists():
        return
    root = vault.resolve()
    for path in vault.rglob("*"):
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if any(p.startswith(".") for p in rel.split("/")) or not keep(path, rel):
            continue
        yield path, rel


def list_vault_folders(vault: Path, *, include_attachments: bool = True) -> list[str]:
    """Return vault-relative folder paths (excludes hidden dirs)."""
    out = {"attachments"} if include_attachments and (vault / "attachments").is_dir() else set()
    for _, rel in _iter_rel(vault, lambda p, _r: p.is_dir()):
        parts = rel.split("/")
        out.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(out)


def list_attachment_files(vault: Path) -> list[dict[str, str]]:
    """List files anywhere under vault/attachments/, including subfolders."""
    found = _iter_rel(vault / "attachments", lambda p, _r: p.is_file())
    return [{"name": p.name, "rel_path": f"attachments/{rel}"} for p, rel in sorted(found, key=lambda t: t[1])]


def list_vault_media_files(vault: Path) -> list[dict[str, str]]:
    """List all non-markdown files in the vault (attachments and elsewhere)."""
    found = _iter_rel(vault, lambda p, r: p.is_file() and not r.lower().endswith(".md"))
    return sorted(({"name": p.name, "rel_path": rel} for p, rel in found), key=lambda f: f["rel_path"].lower())


def iter_vault_md_files(vault: Path) -> list[str]:
    """Return vault-relative paths for all note markdown files (attachments excluded)."""
    found = _iter_rel(vault, lambda p, r: r.lower().endswith(".md") and "/attachments/" not in f"/{r}/")
    return sorted(rel for _, rel in found)


async def sync_vault_notes(db: AsyncSession, kb: KBContext) -> dict[str, int]:
    """Async wrapper used by notes list / setup."""
    vault = Path(kb.vault_path) if kb.vault_path else None
    if not vault or not vault.exists():
        return {"files": 0, "created": 0, "updated": 0}

    rels = iter_vault_md_files(vault)
    existing = list(
        (await db.execute(select(Note).where(Note.kb_id == kb.kb_id))).scalars().all()
    )
    by_rel = { (n.rel_path or "").replace("\\", "/"): n for n in existing if n.rel_path }
    by_title = { (n.title or "").lower(): n for n in existing if n.title }
    on_disk = set(rels)

    def adoptable(stem: str) -> Note | None:
        """A root-level note whose own file is gone — this nested file is it, moved.

        Without the on-disk check a second note of the same name in another folder
        would steal the root note's row and orphan the root file.
        """
        row = by_title.get(stem)
        if row is None or not row.rel_path:
            return None
        current = row.rel_path.replace("\\", "/")
        if "/" in current or current in on_disk:
            return None
        return row

    created = 0
    updated = 0
    for rel in rels:
        title = title_from_filename(rel)
        row = by_rel.get(rel)
        if row is None:
            stem = Path(rel).stem.lower()
            row = adoptable(stem)
            if row is not None:
                by_title.pop(stem, None)
                row.rel_path = rel
                if not (row.title or "").strip():
                    row.title = title
                row.updated_at = datetime.now(timezone.utc)
                updated += 1
                by_rel[rel] = row
                continue
            row = Note(
                kb_id=kb.kb_id,
                title=title,
                rel_path=rel,
                content="",
                processed=False,
                processing_stage="Saved",
            )
            db.add(row)
            created += 1
            by_rel[rel] = row
            by_title[title.lower()] = row
        elif not (row.title or "").strip():
            # Display title is user-owned — don't clobber from filename
            row.title = title
            updated += 1
    await db.commit()
    return {"files": len(rels), "created": created, "updated": updated}
