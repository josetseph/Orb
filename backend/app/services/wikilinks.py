"""Sync note_links table from note markdown wikilinks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.note import Note
from app.models.wikilink import NoteLink
from app.services.kb_registry import KBContext
from app.services.note_files import note_body
from app.services.vault import extract_wikilinks


def _normalize_link(value: str | None) -> str:
    """Lowercase, forward-slashed, extension-less form used for matching."""
    text = (value or "").replace("\\", "/").strip().strip("/").lower()
    while text.startswith("./"):
        text = text[2:]
    if text.endswith(".md"):
        text = text[:-3]
    return text


def _folder_of(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def _folder_proximity(source_dir: str, target_dir: str) -> tuple[int, int]:
    """Closeness of two vault folders as ``(steps, -shared_depth)``, lower is nearer.

    Steps are tree hops (same folder 0, parent/child 1, sibling 2); shared depth
    breaks ties so a sibling folder beats an unrelated one the same distance away.
    """
    a = source_dir.split("/") if source_dir else []
    b = target_dir.split("/") if target_dir else []
    shared = 0
    while shared < len(a) and shared < len(b) and a[shared] == b[shared]:
        shared += 1
    return (len(a) - shared) + (len(b) - shared), -shared


@dataclass(frozen=True)
class _Candidate:
    note_id: str
    rel_path: str  # normalized, extension-less; "" when the note has no path


class WikilinkResolver:
    """Resolve ``[[targets]]`` to note ids, disambiguating duplicate names by folder.

    Exact vault paths win first (so ``[[photosynthesis]]`` and
    ``[[meow/photosynthesis]]`` stay distinct). Bare names with no exact path
    match fall back to same-folder, then nearest-folder proximity.

    Build once per note set, then call :meth:`resolve` for every link.
    """

    def __init__(self, notes: list[Note]) -> None:
        self._by_path: dict[str, str] = {}
        self._by_name: dict[str, list[_Candidate]] = {}
        self._candidates: list[_Candidate] = []
        self._rel_by_id: dict[str, str | None] = {}
        for note in notes:
            rel = _normalize_link(note.rel_path)
            candidate = _Candidate(note_id=note.id, rel_path=rel)
            self._candidates.append(candidate)
            self._rel_by_id[note.id] = note.rel_path
            if rel:
                self._by_path.setdefault(rel, note.id)
            names = {rel.rsplit("/", 1)[-1] if rel else "", _normalize_link(note.title)}
            for name in names:
                if name:
                    self._by_name.setdefault(name, []).append(candidate)

    def source_rel_path(self, source_note_id: str) -> str | None:
        return self._rel_by_id.get(source_note_id)

    def resolve(self, target: str, source_rel_path: str | None = None) -> str | None:
        key = _normalize_link(target)
        if not key:
            return None
        source_dir = _folder_of(_normalize_link(source_rel_path))

        # Exact vault path (incl. root-level basename) always wins so
        # ``[[photosynthesis]]`` and ``[[meow/photosynthesis]]`` stay distinct.
        if key in self._by_path:
            return self._by_path[key]

        if "/" in key:
            suffix = f"/{key}"
            partial = [c for c in self._candidates if c.rel_path.endswith(suffix)]
            best = self._best(partial, source_dir)
            if best:
                return best

        # Paths written relative to the linking note's own folder.
        if source_dir and f"{source_dir}/{key}" in self._by_path:
            return self._by_path[f"{source_dir}/{key}"]

        return self._best(self._by_name.get(key.rsplit("/", 1)[-1], []), source_dir)

    @staticmethod
    def _best(candidates: list[_Candidate], source_dir: str) -> str | None:
        if not candidates:
            return None
        # Nearest folder wins; shallower and then alphabetical keep it deterministic.
        ranked = sorted(
            candidates,
            key=lambda c: (
                *_folder_proximity(source_dir, _folder_of(c.rel_path)),
                c.rel_path.count("/"),
                c.rel_path,
            ),
        )
        return ranked[0].note_id


def _apply_note_links(
    *,
    source_note_id: str,
    content: str,
    resolver: WikilinkResolver,
    add_link: Callable[[str, str | None], None],
) -> None:
    """Parse ``content`` and emit one link per unique target via ``add_link``.

    Callers clear existing rows for ``source_note_id`` before invoking this.
    """
    links = extract_wikilinks(content)
    if not links:
        return

    source_rel = resolver.source_rel_path(source_note_id)
    seen: set[str] = set()
    for target_title, _alias in links:
        key = _normalize_link(target_title)
        if not key or key in seen:
            continue
        seen.add(key)
        add_link(target_title, resolver.resolve(target_title, source_rel))


async def refresh_note_links(
    db: AsyncSession,
    kb_id: str,
    source_note_id: str,
    content: str,
    *,
    notes: list[Note] | None = None,
    resolver: WikilinkResolver | None = None,
) -> None:
    """Replace all outgoing wikilinks for a note."""
    await db.run_sync(
        refresh_note_links_sync, kb_id, source_note_id, content, notes=notes, resolver=resolver
    )


def refresh_note_links_sync(
    session: Session,
    kb_id: str,
    source_note_id: str,
    content: str,
    *,
    notes: list[Note] | None = None,
    resolver: WikilinkResolver | None = None,
) -> None:
    """Sync variant for vault watcher / non-async contexts."""
    if notes is None:
        notes = list(
            session.execute(select(Note).where(Note.kb_id == kb_id)).scalars().all()
        )
    if resolver is None:
        resolver = WikilinkResolver(notes)

    session.execute(
        delete(NoteLink).where(
            NoteLink.kb_id == kb_id,
            NoteLink.source_note_id == source_note_id,
        )
    )

    def add_link(target_title: str, target_note_id: str | None) -> None:
        session.add(
            NoteLink(
                kb_id=kb_id,
                source_note_id=source_note_id,
                target_title=target_title,
                target_note_id=target_note_id,
            )
        )

    _apply_note_links(
        source_note_id=source_note_id,
        content=content,
        resolver=resolver,
        add_link=add_link,
    )


async def rebuild_kb_note_links(db: AsyncSession, kb: KBContext) -> dict[str, int]:
    """Re-parse every note body (vault-backed) into note_links."""
    notes = list(
        (await db.execute(select(Note).where(Note.kb_id == kb.kb_id))).scalars().all()
    )
    # One index + one notes query for the whole vault — not per note.
    resolver = WikilinkResolver(notes)
    for note in notes:
        content = note_body(note, kb)
        await refresh_note_links(
            db,
            kb.kb_id,
            note.id,
            content,
            notes=notes,
            resolver=resolver,
        )
    await db.commit()
    link_count = (
        await db.execute(select(NoteLink).where(NoteLink.kb_id == kb.kb_id))
    ).scalars().all()
    return {"notes": len(notes), "links": len(list(link_count))}


async def notes_graph_payload(db: AsyncSession, kb_id: str) -> dict:
    """Nodes + edges for notes-only graph view."""
    notes = list(
        (await db.execute(select(Note).where(Note.kb_id == kb_id))).scalars().all()
    )
    links = list(
        (await db.execute(select(NoteLink).where(NoteLink.kb_id == kb_id))).scalars().all()
    )
    nodes = [
        {
            "id": n.id,
            "title": n.title or n.rel_path or n.id,
            "type": "note",
            "rel_path": n.rel_path,
        }
        for n in notes
    ]
    known = {n.id for n in notes}
    for link in links:
        if not link.target_note_id:
            phantom_id = f"missing:{link.target_title}"
            if phantom_id not in known:
                nodes.append(
                    {
                        "id": phantom_id,
                        "title": link.target_title,
                        "type": "missing",
                        "rel_path": None,
                    }
                )
                known.add(phantom_id)

    edges = []
    for link in links:
        tid = link.target_note_id or f"missing:{link.target_title}"
        edges.append(
            {
                "source": link.source_note_id,
                "target": tid,
                "type": "wikilink",
            }
        )
    return {"nodes": nodes, "edges": edges}


async def note_neighborhood_payload(
    db: AsyncSession,
    kb_id: str,
    note_id: str,
) -> dict:
    """Local graph: selected note + direct wikilink neighbors."""
    full = await notes_graph_payload(db, kb_id)
    neighbor_ids = {note_id}
    for edge in full["edges"]:
        if edge["source"] == note_id:
            neighbor_ids.add(edge["target"])
        if edge["target"] == note_id:
            neighbor_ids.add(edge["source"])
    nodes = [n for n in full["nodes"] if n["id"] in neighbor_ids]
    edges = [
        e
        for e in full["edges"]
        if e["source"] in neighbor_ids and e["target"] in neighbor_ids
    ]
    return {"nodes": nodes, "edges": edges, "center_id": note_id}
