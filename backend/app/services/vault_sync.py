"""Reconcile SQLite note rows with markdown files on disk (folder paths included)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import get_logger
from app.models.note import Note
from app.services.kb_registry import KBContext
from app.services.vault import mark_self_write, title_from_filename

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


def iter_vault_md_files(vault: Path) -> list[str]:
    """Return vault-relative paths for all note markdown files (attachments excluded)."""
    found = _iter_rel(vault, lambda p, r: r.lower().endswith(".md") and "/attachments/" not in f"/{r}/")
    return sorted(rel for _, rel in found)


# ``](target)`` and the extraction marker's ``src="…"`` — the marker must keep
# matching its link or ingestion re-transcribes the attachment.
_TARGET_OPEN = r'(\]\(|orb:extract src=")'
# A target may hold balanced parens (``encodeURI`` leaves them raw).
_VAULT_TARGET_RE = re.compile(
    _TARGET_OPEN + r'(/vault-files/[^/)"]+/)((?:[^()"\n]|\([^()"\n]*\))+)'
)
_BARE_DOUBLED_RE = re.compile(_TARGET_OPEN + r"attachments/attachments/")


def _normalize_vault_targets(text: str) -> str:
    """Collapse ``attachments/attachments/`` and rewrite ``/vault-files/<any kb>/<rel>``
    to the canonical vault-relative ``<rel>`` (each segment ``quote(seg, safe="")``,
    the form ``vault_ops.rewrite_refs_in_text`` writes). The kb id is minted per
    workspace row, so an absolute link died with every re-created workspace."""

    def _fix(m: re.Match[str]) -> str:
        segs = [unquote(s) for s in m.group(3).split("/")]
        if segs[:2] == ["attachments", "attachments"]:
            del segs[0]
        return m.group(1) + "/".join(quote(s, safe="") for s in segs)

    return _BARE_DOUBLED_RE.sub(r"\1attachments/", _VAULT_TARGET_RE.sub(_fix, text))


_ATTACHMENT_LINK_RE = re.compile(r"\]\((attachments/[^)\s]*(?:\([^)]*\)[^)\s]*)*)\)")


def migrate_vault_files(vault: Path) -> int:
    """One-time in-place sweep of legacy vault shapes; gated by ``.orb/migrated-v4``.

    v2 added the relative-link rewrite, v3 moves stray non-markdown files under
    ``attachments/``, v4 re-points links whose file was moved inside
    ``attachments/`` by an older build that failed to rewrite them. Every step
    is idempotent, so an older vault simply runs the whole sweep once more.
    """
    marker = vault / ".orb" / "migrated-v4"
    if marker.exists():
        return 0
    from app.services.vault_ops import rewrite_refs_in_text, unique_rel_path
    from app.workflows.agents.ingestion_agent import wrap_legacy_enrichment_blocks

    # Attachments live only under attachments/ — anything else non-markdown moves there.
    stray = list(_iter_rel(vault, lambda p, r: p.is_file() and not r.lower().endswith(".md") and not r.startswith("attachments/")))
    moved: list[tuple[str, str]] = []
    for path, rel in stray:
        new_rel = unique_rel_path(vault, f"attachments/{rel}")
        (vault / new_rel).parent.mkdir(parents=True, exist_ok=True)
        path.rename(vault / new_rel)
        moved.append((rel, new_rel))

    # Upload names end in a random 8-hex suffix, so a filename identifies one
    # attachment wherever it sits: a link to a missing path is re-pointed when
    # exactly one file under attachments/ carries that name.
    by_name: dict[str, list[str]] = {}
    for _path, rel in _iter_rel(vault, lambda p, r: p.is_file() and r.startswith("attachments/")):
        by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)

    rewritten = 0
    for rel in iter_vault_md_files(vault):
        path = vault / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fixed = wrap_legacy_enrichment_blocks(_normalize_vault_targets(text))
        for old_rel, new_rel in moved:
            fixed = rewrite_refs_in_text(fixed, old_rel, new_rel)
        for target in set(_ATTACHMENT_LINK_RE.findall(fixed)):
            old_rel = unquote(target)
            homes = by_name.get(old_rel.rsplit("/", 1)[-1], [])
            if len(homes) == 1 and homes[0] != old_rel and not (vault / old_rel).exists():
                fixed = rewrite_refs_in_text(fixed, old_rel, homes[0])
        if fixed != text:
            mark_self_write(vault, rel)
            path.write_text(fixed, encoding="utf-8")
            rewritten += 1
    marker.parent.mkdir(exist_ok=True)
    marker.touch()
    logger.info("Vault migration v4 (%s): %d files moved, %d files rewritten", vault, len(moved), rewritten)
    return rewritten


async def sync_vault_notes(db: AsyncSession, kb: KBContext) -> dict[str, int]:
    """Async wrapper used by notes list / setup."""
    vault = Path(kb.vault_path) if kb.vault_path else None
    if not vault or not vault.exists():
        return {"files": 0, "created": 0, "updated": 0}

    await asyncio.to_thread(migrate_vault_files, vault)
    existing = list(
        (await db.execute(select(Note).where(Note.kb_id == kb.kb_id))).scalars().all()
    )
    rels = iter_vault_md_files(vault)
    by_rel = {n.rel_path: n for n in existing if n.rel_path}
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
        if "/" in row.rel_path or row.rel_path in on_disk:
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
