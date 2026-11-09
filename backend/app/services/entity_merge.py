"""Fold one entity into another (B into A), and take that back.

B's relations and contexts move to A, B's names become aliases of A, and B leaves the
graph, the vector store and the keyword index. Deciding *which* entities are the same is
not done here.

A merge is a durable job (services/jobs.py) that waits its turn between note writes. Its
plan is computed once, before the first change, and every step is safe to repeat: points
are written under ids derived from their content before their sources are deleted, so a
crash at any step resumes to the same result and loses nothing.

Undo puts back what the merge moved. What notes wrote to A after the merge stays on A.
A merge can only be undone while A still exists: undo a later merge of A first.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal
from app.core.log import get_logger
from app.models.entity_merge import EntityMerge
from app.services import jobs
from app.services.embedding import embedding_service

logger = get_logger("EntityMerge")


async def request_merge(kb_id: str, keep_id: str, absorbed_id: str) -> int:
    """Queue folding ``absorbed_id`` into ``keep_id``; returns the merge's id. Asking again
    after a failure continues the same merge from its saved plan."""
    async with AsyncSessionLocal() as session:
        record = (
            await session.execute(
                select(EntityMerge).where(
                    EntityMerge.kb_id == kb_id,
                    EntityMerge.keep_id == keep_id,
                    EntityMerge.absorbed_id == absorbed_id,
                    EntityMerge.state == "queued",
                )
            )
        ).scalars().first()
        if record is None:
            record = EntityMerge(kb_id=kb_id, keep_id=keep_id, absorbed_id=absorbed_id)
            session.add(record)
            await session.flush()
        merge_id = record.id
        await jobs.enqueue(
            session, jobs.MERGE_ENTITY, kb_id, absorbed_id, jobs.input_hash(keep_id), {"merge_id": merge_id}
        )
        await session.commit()
    jobs.wake()
    return merge_id


async def request_undo(merge_id: int) -> None:
    """Queue undoing a finished merge."""
    async with AsyncSessionLocal() as session:
        record = await session.get(EntityMerge, merge_id)
        if record is None or record.state != "merged":
            raise ValueError(f"merge {merge_id} is not a finished merge")
        await jobs.enqueue(
            session, jobs.UNMERGE_ENTITY, record.kb_id, record.absorbed_id, jobs.input_hash(str(merge_id)),
            {"merge_id": merge_id},
        )
        await session.commit()
    jobs.wake()


def prepare(job):
    """The job worker's entry for a merge or an undo."""
    from app.services.kb_registry import kb_registry

    kb = kb_registry.get_kb(job.kb_id)
    if kb is None:
        return None
    workflow, merge_id = kb.get_ingestion_workflow(), job.payload["merge_id"]
    step = merge if job.kind == jobs.MERGE_ENTITY else undo

    async def run() -> str:
        await step(workflow, merge_id)
        return "done"

    return run


async def _record(merge_id: int) -> EntityMerge:
    async with AsyncSessionLocal() as session:
        record = await session.get(EntityMerge, merge_id)
    if record is None:
        raise ValueError(f"merge {merge_id} does not exist")
    return record


async def _save(merge_id: int, **values) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(update(EntityMerge).where(EntityMerge.id == merge_id).values(**values))
        await session.commit()


async def merge(workflow, merge_id: int) -> None:
    """Fold the entity in, resuming from wherever an earlier run stopped."""
    # pylint: disable=protected-access
    graph, qdrant, meili = workflow._graph, workflow._qdrant, workflow._meili

    async def work() -> None:
        record = await _record(merge_id)
        plan = record.plan
        if plan is None:
            plan = await asyncio.to_thread(_plan, graph, qdrant, record.keep_id, record.absorbed_id)
            await _save(merge_id, plan=plan)
        await asyncio.to_thread(_apply, graph, qdrant, meili, plan)
        await workflow._reindex_node(plan["keep"]["id"])
        await _save(merge_id, state="merged")
        logger.info("Merged '%s' into '%s'", plan["absorbed"]["name"], plan["keep"]["name"])

    await workflow.between_note_writes(work)


async def undo(workflow, merge_id: int) -> None:
    """Take a finished merge back, resuming from wherever an earlier run stopped."""
    # pylint: disable=protected-access
    graph, qdrant = workflow._graph, workflow._qdrant

    async def work() -> None:
        record = await _record(merge_id)
        if record.state != "merged":
            raise ValueError(f"merge {merge_id} is not a finished merge")
        plan = record.plan
        await asyncio.to_thread(_restore, graph, qdrant, plan)
        await workflow._reindex_node(plan["keep"]["id"])
        await workflow._reindex_node(plan["absorbed"]["id"])
        await _save(merge_id, state="undone")
        logger.info("Undid merging '%s' into '%s'", plan["absorbed"]["name"], plan["keep"]["name"])

    await workflow.between_note_writes(work)


def _plan(graph, qdrant, keep_id: str, absorbed_id: str) -> dict:
    """Everything the merge will do, and what undoing it needs. Changes nothing."""
    keep, absorbed = graph.get_entity(keep_id), graph.get_entity(absorbed_id)
    if keep is None or absorbed is None or keep_id == absorbed_id:
        raise ValueError(f"cannot merge {absorbed_id} into {keep_id}: need two existing entities")
    for entity in (keep, absorbed):
        if entity["kind"] != "indexable" or entity["type"] == "document":
            raise ValueError(f"'{entity['name']}' is not an entity, so it cannot be merged")

    on_keep = {(e["source"], e["target"], e["rel_type"]): e["note_ids"] or [] for e in graph.entity_edges(keep_id)}
    edges = []
    raw = graph.entity_edges(absorbed_id)
    texts = qdrant.relationship_texts([e["relationship_id"] for e in raw if e["relationship_id"]])
    for edge in raw:
        source = keep_id if edge["source"] == absorbed_id else edge["source"]
        target = keep_id if edge["target"] == absorbed_id else edge["target"]
        notes = edge["note_ids"] or []
        loop = source == target  # an edge between the two: nothing to keep once they are one
        key = (source, target, edge["rel_type"])
        created = not loop and key not in on_keep
        added = [] if loop else [n for n in notes if n not in on_keep.get(key, [])]
        if not loop:
            on_keep[key] = on_keep.get(key, []) + added
        edges.append({
            **edge,
            "new_source": None if loop else source,
            "new_target": None if loop else target,
            "created": created,
            "added": added,
            "text": texts.get(edge["relationship_id"], ""),
        })
    references = graph.note_references(absorbed_id)
    keep_references = set(graph.note_references(keep_id))
    return {
        "keep": keep,
        "absorbed": absorbed,
        "absorbed_aliases": graph.aliases_of(absorbed_id),
        "references": references,
        "only_absorbed_references": [n for n in references if n not in keep_references],
        "edges": edges,
        "contexts": qdrant.context_moves(absorbed_id, keep_id),
    }


def _apply(graph, qdrant, meili, plan: dict) -> None:
    keep_id, absorbed_id = plan["keep"]["id"], plan["absorbed"]["id"]
    for edge in plan["edges"]:
        if edge["new_source"] is None:
            continue
        graph.absorb_edge(edge["new_source"], edge["new_target"], edge)
        # A point not moved here (the edge joined one A already had) goes with B below.
        if edge["created"] and edge["relationship_id"]:
            qdrant.retarget_relationship(edge["relationship_id"], edge["new_source"], edge["new_target"])
    graph.add_references(plan["references"], keep_id)
    qdrant.move_context_points([(m["from"], m["to"], False) for m in plan["contexts"]], keep_id)
    graph.set_aliases([plan["absorbed"]["name"], *plan["absorbed_aliases"]], keep_id)
    # Last, so a crash before this point still has B to finish from.
    graph.delete_entity(absorbed_id)
    qdrant.delete_node(absorbed_id)
    meili.delete_node(absorbed_id)


def _restore(graph, qdrant, plan: dict) -> None:
    keep, absorbed = plan["keep"], plan["absorbed"]
    if graph.get_entity(keep["id"]) is None:
        raise ValueError(f"'{keep['name']}' was merged away too: undo that merge first")
    graph.restore_entity(absorbed)
    # Stands in until the caller re-indexes B from the contexts moved back below.
    if not qdrant.upsert_node_core(
        node_id=absorbed["id"], name=absorbed["name"], node_type=absorbed["type"],
        description_vector=embedding_service.embed_documents([absorbed["name"]])[0],
    ):
        raise RuntimeError(f"could not restore '{absorbed['name']}' in the vector store")
    graph.drop_aliases([absorbed["name"]])
    graph.set_aliases(plan["absorbed_aliases"], absorbed["id"])
    graph.add_references(plan["references"], absorbed["id"])
    graph.drop_references(plan["only_absorbed_references"], keep["id"])

    lost = []
    for edge in plan["edges"]:
        graph.absorb_edge(edge["source"], edge["target"], edge)
        if edge["new_source"] is not None and (edge["created"] or edge["added"]):
            graph.retract_edge(edge["new_source"], edge["new_target"], edge["rel_type"], edge["added"], edge["created"])
        if not edge["relationship_id"] or not edge["text"]:
            continue
        if edge["created"]:
            qdrant.retarget_relationship(edge["relationship_id"], edge["source"], edge["target"])
        else:
            lost.append(edge)  # its point was deleted with B
    if lost and not qdrant.upsert_node_relationships([
        {
            "relationship_id": edge["relationship_id"],
            "natural_language": edge["text"],
            "nl_vector": vector,
            "source_node_id": edge["source"],
            "target_node_id": edge["target"],
        }
        for edge, vector in zip(lost, embedding_service.embed_documents([e["text"] for e in lost]))
    ]):
        raise RuntimeError(f"could not restore the relations of '{absorbed['name']}'")
    qdrant.move_context_points([(m["to"], m["from"], m["shared"]) for m in plan["contexts"]], absorbed["id"])
