"""Knowledge-graph visualization and entity endpoints."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_kb
from app.core.log import get_logger
from app.services.ai_gate import ai_is_configured
from app.services.kb_registry import KBContext

logger = get_logger("API")
router = APIRouter()


class ScanTextInput(BaseModel):
    """Request body for scanning a text block for entity mentions."""

    text: str


def _needs_title(name: object) -> bool:
    n = (str(name) if name is not None else "").strip()
    return not n or n.lower() in {"unknown", "untitled", "untitled note"}


def _apply_meili_content(detail: dict, doc: dict) -> None:
    if not detail.get("name") and doc.get("name"):
        detail["name"] = doc["name"]
    if not detail.get("node_type") and doc.get("type"):
        detail["node_type"] = doc["type"]
    ctx = doc.get("isolated_contexts") or []
    if isinstance(ctx, str):  # indexed before contexts were stored as a list
        ctx = [ctx]
    ctx = [str(x) for x in ctx if x]
    if ctx:
        detail["isolated_contexts"] = ctx
        if not detail.get("description"):
            detail["description"] = ctx[0]
            detail["summary"] = ctx[0]


@router.get("/api/v1/graph/3d/full")
async def graph_3d_full(kb: KBContext = Depends(get_kb)):
    """
    Return ALL nodes and ALL edges for the 3D graph, with solar-system
    positions computed per request. Used by the renderer that shows
    everything at once.
    """
    return await asyncio.to_thread(kb.graph.get_full_3d_graph)


@router.get("/api/v1/graph/3d/node/{node_id}")
async def graph_3d_node_detail(node_id: str, kb: KBContext = Depends(get_kb)):
    """
    Return full detail for a single Indexable node (description, contexts, connections).
    Called on-demand when the user clicks a card in the 3D graph.
    """
    detail = await asyncio.to_thread(kb.graph.get_node_detail, node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Node not found")

    # Meili fallback when Qdrant has structural presence but empty content payloads.
    needs_content = not (detail.get("description") or detail.get("isolated_contexts"))
    if needs_content:
        try:
            doc = await asyncio.to_thread(kb.meili.get_node, node_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("[graph] Meili content fallback failed: %s", exc)
            doc = None
        if isinstance(doc, dict):
            _apply_meili_content(detail, doc)

    # Legacy note nodes were named at startup (main._migrate_stores); anything
    # still blank has no SQLite row to name it from.
    for note in detail.get("related_notes") or []:
        if _needs_title(note.get("name")):
            note["name"] = "Untitled note"
    for conn in detail.get("connections") or []:
        if _needs_title(conn.get("name")):
            conn["name"] = "Untitled note" if conn.get("kind") == "note" else "Untitled"

    return detail


@router.get("/api/v1/graph/entities/search")
async def search_entities_autocomplete(
    q: str,
    limit: int = 5,
    kb: KBContext = Depends(get_kb),
):
    """
    Search entity nodes by name for autocomplete suggestions in the notes editor.
    Returns only indexable entities (excludes notes and community nodes).
    """
    if not q or len(q.strip()) < 2:
        return []
    if not ai_is_configured():
        return []

    hits = await asyncio.to_thread(kb.meili.search_nodes, q.strip(), limit * 2)
    results: list[dict] = []
    for hit in hits:
        payload = hit.get("payload", {})
        node_type = payload.get("type", "")
        node_id = payload.get("node_id", "")
        name = payload.get("name", "")
        if not node_id or not name:
            continue
        if node_type in ("note", "community"):
            continue
        results.append({"node_id": node_id, "name": name, "node_type": node_type})
        if len(results) >= limit:
            break
    return results


@router.post("/api/v1/graph/entities/scan-text")
async def scan_entities_in_text(
    body: ScanTextInput,
    kb: KBContext = Depends(get_kb),
):
    """
    Scan a text block for entity mentions and return found entities.
    Used to auto-highlight entity names in existing notes on load.
    """
    text = body.text
    if not text or len(text.strip()) < 3:
        return []

    candidates: set[str] = set()
    # Multi-word proper noun sequences: "Clara Sydney", "Project Horizon"
    candidates.update(re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text))
    # Single capitalised words of 4+ chars (potential proper nouns)
    candidates.update(re.findall(r"\b([A-Z][a-z]{3,})\b", text))
    if not candidates:
        return []

    found: dict[str, dict] = {}
    for candidate in list(candidates)[:40]:
        hits = await asyncio.to_thread(kb.meili.search_nodes, candidate, 2)
        for hit in hits:
            payload = hit.get("payload", {})
            name = payload.get("name", "")
            node_type = payload.get("type", "")
            node_id = payload.get("node_id", "")
            if not node_id or node_type in ("note", "community"):
                continue
            if name.lower() in text.lower() and node_id not in found:
                found[node_id] = {
                    "node_id": node_id,
                    "name": name,
                    "node_type": node_type,
                }

    return list(found.values())


@router.post("/api/v1/graph/entities/note-subgraph")
async def note_entity_subgraph(
    body: ScanTextInput,
    kb: KBContext = Depends(get_kb),
):
    """
    Entities mentioned in note text + knowledge-graph edges between them.
    Used by the Connected panel "Nodes" mode.
    """
    entities = await scan_entities_in_text(body, kb)
    nodes = [
        {
            "id": e["node_id"],
            "title": e["name"],
            "type": e.get("node_type") or "entity",
        }
        for e in entities
    ]
    id_set = {n["id"] for n in nodes}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    if ai_is_configured() and id_set:
        graph = kb.graph
        for ent in entities:
            try:
                related = await asyncio.to_thread(
                    lambda name=ent["name"]: graph.get_related_nodes(
                        name, max_depth=1
                    )
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("[graph] related nodes failed for %s: %s", ent["name"], exc)
                related = []
            for rel in related:
                tid = rel.get("node_id")
                if not tid or tid not in id_set or tid == ent["node_id"]:
                    continue
                a, b = sorted((ent["node_id"], tid))
                if (a, b) in seen_edges:
                    continue
                seen_edges.add((a, b))
                path = rel.get("relationship_path")
                edge_type = (
                    path[0]
                    if isinstance(path, list) and path
                    else "related"
                )
                edges.append(
                    {
                        "source": ent["node_id"],
                        "target": tid,
                        "type": edge_type,
                    }
                )

    return {"nodes": nodes, "edges": edges, "center_id": None}
