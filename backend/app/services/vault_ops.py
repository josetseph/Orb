"""Move / rename vault files and rewrite markdown references."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note
from app.services.kb_registry import KBContext
from app.services.note_files import note_body, persist_note_body
from app.services.vault import mark_self_write, sanitize_title, title_from_filename
from app.services.wikilinks import WikilinkResolver, _normalize_link


def _norm(rel: str) -> str:
    return (rel or "").replace("\\", "/").lstrip("/")


def _in_attachments(rel: str) -> bool:
    return rel == "attachments" or rel.startswith("attachments/")


def safe_vault_join(vault: Path, rel: str) -> Path:
    full = (vault / _norm(rel)).resolve()
    root = vault.resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes vault") from exc
    return full


def unique_rel_path(vault: Path, desired_rel: str) -> str:
    """If ``desired_rel`` exists, append `` 2``, `` 3``, … before the suffix."""
    rel = _norm(desired_rel)
    if not (vault / rel).exists():
        return rel
    path = Path(rel)
    stem, suffix = path.stem, path.suffix
    parent = str(path.parent).replace("\\", "/")
    if parent == ".":
        parent = ""
    n = 2
    while True:
        name = f"{stem} {n}{suffix}"
        candidate = f"{parent}/{name}" if parent else name
        if not (vault / candidate).exists():
            return candidate
        n += 1


def rewrite_refs_in_text(content: str, old_rel: str, new_rel: str) -> str:
    """Rewrite attachment / vault-file links when a file moves.

    Only rewrites markdown link/image *targets* — never a bare substring replace,
    which would turn ``attachments/x.mp4`` into ``attachments/attachments/x.mp4``
    when ``old_rel`` is just the filename. Matches the canonical vault-relative
    form and any legacy ``/vault-files/<kb>/`` prefix (whatever kb id it holds);
    always emits the canonical form: relative, each segment ``quote(seg, safe="")``.
    """
    old = _norm(old_rel)
    new = _norm(new_rel)
    if not old or old == new or not content:
        return content

    from urllib.parse import quote

    encoded_old = "/".join(quote(seg, safe="") for seg in old.split("/"))
    encoded_new = "/".join(quote(seg, safe="") for seg in new.split("/"))

    # ](...target...) and the extraction marker's src="..." — the marker must
    # follow the link: it is how ingestion and the media widget know the
    # attachment was already processed, and a stale one meant a moved
    # recording got transcribed all over again.
    return re.sub(
        rf'(\]\(|orb:extract src=")(?:/vault-files/[^/)"]+/)?'
        rf'(?:{re.escape(old)}|{re.escape(encoded_old)})(\)|")',
        lambda m: f"{m.group(1)}{encoded_new}{m.group(2)}",
        content,
    )


_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\]|#]+)((?:#[^\]|]*)?(?:\|[^\]]*)?)\]\]")


def rewrite_wikilinks_in_text(
    content: str,
    resolver: WikilinkResolver,
    source_rel_path: str | None,
    moved_note_id: str,
    bare_target: str,
    path_target: str,
) -> str:
    """Repoint ``[[links]]`` that resolved to the moved note at its new name.

    ``resolver`` must be built from the pre-move note set so old targets still
    resolve. Bare targets are rewritten to ``bare_target``, path-style targets
    to ``path_target``; headings and aliases are preserved.
    """
    if not content or "[[" not in content:
        return content

    def _sub(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        if not target or resolver.resolve(target, source_rel_path) != moved_note_id:
            return match.group(0)
        new_target = path_target if "/" in target else bare_target
        if new_target == target:
            return match.group(0)
        return f"[[{new_target}{match.group(2) or ''}]]"

    return _WIKILINK_TARGET_RE.sub(_sub, content)


def strip_refs_in_text(content: str, rel: str, kb_id: str) -> str:
    """Remove markdown image/link references that point at ``rel``."""
    old = _norm(rel)
    if not old or not content:
        return content
    targets = {
        old,
        f"/vault-files/{kb_id}/{old}",
        Path(old).name,
    }
    # Also match percent-encoded path variants used in markdown
    from urllib.parse import quote

    encoded = "/".join(quote(seg, safe="") for seg in old.split("/"))
    targets.add(encoded)
    targets.add(f"/vault-files/{kb_id}/{encoded}")

    text = content
    for target in targets:
        if not target:
            continue
        escaped = re.escape(target)
        text = re.sub(
            rf"!\[[^\]]*\]\([^)]*{escaped}[^)]*\)",
            "",
            text,
        )
        text = re.sub(
            rf"\[[^\]]*\]\([^)]*{escaped}[^)]*\)",
            "",
            text,
        )
    # Collapse leftover blank runs from removed embeds
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


async def strip_refs_across_notes(
    db: AsyncSession,
    kb: KBContext,
    rel: str,
) -> int:
    notes = list(
        (await db.execute(select(Note).where(Note.kb_id == kb.kb_id))).scalars().all()
    )
    changed = 0
    for note in notes:
        body = note_body(note, kb)
        updated = strip_refs_in_text(body, rel, kb.kb_id)
        if updated != body:
            persist_note_body(note, kb, updated)
            changed += 1
    return changed


async def delete_vault_file(
    db: AsyncSession,
    kb: KBContext,
    rel: str,
) -> dict:
    """Delete a vault attachment (not a note .md) and strip markdown links."""
    vault = Path(kb.vault_path)
    src_rel = _norm(rel)
    if not src_rel or ".." in src_rel.split("/"):
        raise ValueError("Invalid path")
    if src_rel.lower().endswith(".md"):
        raise ValueError("Use note delete for markdown files")

    src = safe_vault_join(vault, src_rel)
    if src.is_dir():
        return await _delete_folder(db, kb, vault, src_rel, src)
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"File not found: {src_rel}")

    mark_self_write(vault, src_rel)
    src.unlink()
    stripped = await strip_refs_across_notes(db, kb, src_rel)
    await db.commit()
    return {"deleted": src_rel, "links_stripped": stripped}


def _files_under(vault: Path, folder: Path) -> list[str]:
    """Vault-relative paths of every visible file below ``folder``."""
    out: list[str] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(vault)).replace("\\", "/")
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        out.append(rel)
    return out


async def _delete_folder(
    db: AsyncSession, kb: KBContext, vault: Path, src_rel: str, src: Path
) -> dict:
    """Delete a folder: notes go through the full note delete, attachments lose their links."""
    from app.api.notes import _delete_note_impl

    notes_deleted = 0
    stripped = 0
    all_notes = list(
        (await db.execute(select(Note).where(Note.kb_id == kb.kb_id))).scalars().all()
    )
    by_rel = {_norm(n.rel_path or ""): n.id for n in all_notes}
    # ponytail: one round-trip per file; fine for a folder, slow for a vault.
    for rel in _files_under(vault, src):
        if rel.lower().endswith(".md"):
            if rel in by_rel:
                await _delete_note_impl(by_rel[rel], db, kb)
                notes_deleted += 1
            continue
        mark_self_write(vault, rel)
        stripped += await strip_refs_across_notes(db, kb, rel)
    await db.commit()
    shutil.rmtree(src)
    return {"deleted": src_rel, "notes_deleted": notes_deleted, "links_stripped": stripped}


async def move_vault_file(
    db: AsyncSession,
    kb: KBContext,
    from_rel: str,
    to_rel: str,
) -> dict:
    """Move any vault file (note .md or attachment) and rewrite links."""
    vault = Path(kb.vault_path)
    src_rel = _norm(from_rel)
    dst_rel = _norm(to_rel)
    if not src_rel or not dst_rel:
        raise ValueError("from_rel and to_rel are required")
    if ".." in src_rel.split("/") or ".." in dst_rel.split("/"):
        raise ValueError("Invalid path")
    if _in_attachments(src_rel) != _in_attachments(dst_rel):
        raise ValueError("Cannot move across the attachments/ boundary")

    src = safe_vault_join(vault, src_rel)
    if src.is_dir():
        return await _move_folder(db, kb, vault, src_rel, dst_rel)
    dst_rel = unique_rel_path(vault, dst_rel)
    dst = safe_vault_join(vault, dst_rel)
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Source not found: {src_rel}")
    if src.resolve() == dst.resolve():
        return {
            "from": src_rel,
            "to": dst_rel,
            "note_id": None,
            "links_rewritten": 0,
        }
    if dst.exists():
        raise FileExistsError(f"Destination exists: {dst_rel}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    mark_self_write(vault, src_rel)
    mark_self_write(vault, dst_rel)
    shutil.move(str(src), str(dst))

    all_notes = list(
        (await db.execute(select(Note).where(Note.kb_id == kb.kb_id))).scalars().all()
    )

    # If this is a note markdown file, update its row
    note_row = None
    resolver = None
    if src_rel.lower().endswith(".md"):
        note_row = next(
            (n for n in all_notes if _norm(n.rel_path or "") == src_rel), None
        )
        if note_row:
            # Snapshot resolution before the path changes so wikilinks that
            # pointed at the old name can follow the note to its new one.
            resolver = WikilinkResolver(all_notes)
            note_row.rel_path = dst_rel
            # Keep the existing display title — never overwrite from filename
            # (users often rename the title without renaming the .md file).
            if not (note_row.title or "").strip():
                note_row.title = title_from_filename(dst_rel)

    bare_target = ""
    path_target = ""
    if note_row and resolver:
        path_target = dst_rel[:-3] if dst_rel.lower().endswith(".md") else dst_rel
        new_stem = Path(dst_rel).stem
        stem_key = _normalize_link(new_stem)
        # Bare-name links stay bare only while the new name is unambiguous.
        ambiguous = any(
            n.id != note_row.id
            and stem_key
            in {
                _normalize_link(n.title),
                _normalize_link(n.rel_path).rsplit("/", 1)[-1],
            }
            for n in all_notes
        )
        bare_target = path_target if ambiguous else new_stem

    rewritten = 0
    for n in all_notes:
        body = note_body(n, kb)
        updated = rewrite_refs_in_text(body, src_rel, dst_rel)
        if note_row and resolver:
            updated = rewrite_wikilinks_in_text(
                updated, resolver, n.rel_path, note_row.id, bare_target, path_target
            )
        if updated != body:
            persist_note_body(n, kb, updated)
            rewritten += 1
    await db.commit()
    if note_row:
        await db.refresh(note_row)

    return {
        "from": src_rel,
        "to": dst_rel,
        "note_id": note_row.id if note_row else None,
        "links_rewritten": rewritten,
    }


async def _move_folder(
    db: AsyncSession, kb: KBContext, vault: Path, src_rel: str, dst_rel: str
) -> dict:
    """Move a folder by moving each file through ``move_vault_file`` so links follow."""
    if dst_rel == src_rel or dst_rel.startswith(src_rel + "/"):
        raise ValueError("Cannot move a folder into itself")
    src = safe_vault_join(vault, src_rel)
    dst_rel = unique_rel_path(vault, dst_rel)
    dst = safe_vault_join(vault, dst_rel)
    rewritten = 0
    moved = 0
    for rel in _files_under(vault, src):
        result = await move_vault_file(db, kb, rel, dst_rel + rel[len(src_rel):])
        rewritten += result["links_rewritten"]
        moved += 1
    # Empty subfolders and dotfiles (.keep) come along; the old tree goes.
    shutil.copytree(src, dst, dirs_exist_ok=True)
    shutil.rmtree(src)
    return {"from": src_rel, "to": dst_rel, "note_id": None, "moved": moved, "links_rewritten": rewritten}


async def rename_note_file_for_title(
    db: AsyncSession,
    kb: KBContext,
    note: Note,
    title: str | None,
) -> bool:
    """Keep the vault filename in sync with the note title (Obsidian parity).

    Renames ``note``'s .md file to the sanitized title in the same folder and
    rewrites markdown refs + wikilinks across the vault. No-op when the stem
    already matches (case-insensitively — a case-only rename on APFS would trip
    the uniquifier into appending `` 2``; the display title keeps the casing).
    """
    current = _norm(note.rel_path or "")
    new_title = (title or "").strip()
    if not current or not new_title or not current.lower().endswith(".md"):
        return False
    desired_stem = sanitize_title(new_title)
    if desired_stem.lower() == Path(current).stem.lower():
        return False
    folder = current.rsplit("/", 1)[0] if "/" in current else ""
    desired_rel = f"{folder}/{desired_stem}.md" if folder else f"{desired_stem}.md"
    await move_vault_file(db, kb, current, desired_rel)
    return True


async def move_note_to_folder(
    db: AsyncSession,
    kb: KBContext,
    note: Note,
    folder: str,
) -> dict:
    """Move a note into ``folder`` keeping the same filename stem."""
    current = _norm(note.rel_path or "")
    if not current:
        raise ValueError("Note has no vault path")
    filename = Path(current).name
    folder_clean = _norm(folder)
    if folder_clean and ".." in folder_clean.split("/"):
        raise ValueError("Invalid folder")
    new_rel = f"{folder_clean}/{filename}" if folder_clean else filename
    if new_rel == current:
        return {"from": current, "to": new_rel, "note_id": note.id, "links_rewritten": 0}
    return await move_vault_file(db, kb, current, new_rel)
