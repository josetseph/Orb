"""Reconcile SQLite note rows with markdown files on disk (folder paths included)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import get_logger
from app.models.note import Note
from app.services.kb_registry import KBContext
from app.services.vault import title_from_filename

logger = get_logger("VaultSync")


def list_vault_folders(vault: Path, *, include_attachments: bool = True) -> list[str]:
    """Return vault-relative folder paths (excludes hidden dirs)."""
    if not vault.exists():
        return []
    out: set[str] = set()
    if include_attachments and (vault / "attachments").is_dir():
        out.add("attachments")
    for path in vault.rglob("*"):
        if not path.is_dir():
            continue
        try:
            rel = str(path.resolve().relative_to(vault.resolve())).replace("\\", "/")
        except ValueError:
            continue
        parts = rel.split("/")
        if any(p.startswith(".") for p in parts):
            continue
        out.add(rel)
        for i in range(1, len(parts)):
            out.add("/".join(parts[:i]))
    return sorted(out)


def list_attachment_files(vault: Path) -> list[dict[str, str]]:
    """List files anywhere under vault/attachments/, including subfolders.

    Recursive on purpose: mkdir and move already support organising
    attachments into folders, but a flat listing made a moved file vanish
    from the UI even though it was still there and still linked.
    """
    att = vault / "attachments"
    if not att.is_dir():
        return []
    files: list[dict[str, str]] = []
    for path in sorted(att.rglob("*")):
        if not path.is_file():
            continue
        try:
            sub = str(path.relative_to(att)).replace("\\", "/")
        except ValueError:
            continue
        if any(part.startswith(".") for part in sub.split("/")):
            continue
        files.append({"name": path.name, "rel_path": f"attachments/{sub}"})
    return files


def list_vault_media_files(vault: Path) -> list[dict[str, str]]:
    """List all non-markdown files in the vault (attachments and elsewhere)."""
    if not vault.exists():
        return []
    files: list[dict[str, str]] = []
    for path in vault.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = str(path.resolve().relative_to(vault.resolve())).replace("\\", "/")
        except ValueError:
            continue
        parts = rel.split("/")
        if any(p.startswith(".") for p in parts):
            continue
        if rel.lower().endswith(".md"):
            continue
        # Skip empty keep files used for empty folders
        if path.name == ".keep":
            continue
        files.append({"name": path.name, "rel_path": rel})
    return sorted(files, key=lambda f: f["rel_path"].lower())


def iter_vault_md_files(vault: Path) -> list[str]:
    """Return vault-relative paths for all note markdown files."""
    if not vault.exists():
        return []
    out: list[str] = []
    for path in vault.rglob("*.md"):
        try:
            rel = str(path.resolve().relative_to(vault.resolve())).replace("\\", "/")
        except ValueError:
            continue
        parts = rel.split("/")
        if any(p.startswith(".") for p in parts):
            continue
        if parts[0] == "attachments" or "/attachments/" in f"/{rel}/":
            continue
        out.append(rel)
    return sorted(out)


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
