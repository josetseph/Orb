"""Ingestion workflow: LLM extraction, graph persistence, embedding, and community detection."""

# pylint: disable=too-many-lines,import-outside-toplevel
import asyncio
import re
import threading
import time
import uuid
from collections import defaultdict

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.log import get_logger
from app.schemas.extraction import Extraction, NoteInput
from app.services.graph import GraphService, graph_service
from app.services.embedding import embedding_service
from app.services.ingestion_tracker import (
    ingestion_tracker as _tracker,
    COMMUNITY_IDLE_SECONDS,
)
from app.services.llm import llm_service
from app.services.qdrant_service import QdrantService, qdrant_service
from app.services.meilisearch_service import MeilisearchService, meilisearch_service
from app.workflows.agents.ingestion_agent import ingestion_agent

logger = get_logger("IngestionPipeline")


def clean_rel_type(rel_type: str, source_name: str, target_name: str) -> str:
    """Remove entity name tokens from a relationship predicate.

    LLMs frequently embed the object (or subject) name into the predicate,
    e.g. "plays_corliss_archer" instead of just "plays".  This function
    strips any token that appears verbatim (case-insensitive) in either
    entity name, then collapses consecutive underscores left by the removal.

    Examples:
        clean_rel_type("plays_corliss_archer", "shirley temple", "corliss archer")
        → "plays"
        clean_rel_type("is_directed_by", "film", "director")
        → "is_directed_by"   (no entity tokens present)
    """
    if not rel_type:
        return rel_type

    # Tokenise entity names into individual words (ignore single-char tokens)
    entity_tokens: set[str] = set()
    for name in (source_name, target_name):
        for token in re.split(r"[\s_\-]+", name.lower()):
            if len(token) > 1:
                entity_tokens.add(token)

    if not entity_tokens:
        return rel_type

    # Tokenise the predicate, drop entity tokens, rejoin
    parts = re.split(r"_", rel_type.lower())
    cleaned = [p for p in parts if p not in entity_tokens]
    if not cleaned:
        # Entire predicate was entity names — fall back to "relates_to"
        return "relates_to"
    return "_".join(cleaned)


class EntityLockManager:  # pylint: disable=too-few-public-methods
    """
    Manages per-entity locks to prevent race conditions during summary updates.
    Ensures that multiple notes updating the same entity wait for each other.
    """

    def __init__(self):
        self._locks = defaultdict(asyncio.Lock)

    def get_lock(self, label: str, name: str):
        """Return the asyncio lock for a given (label, name) entity pair."""
        return self._locks[(label, name.lower().strip())]


class IngestionWorkflow:
    """Orchestrates full ingestion: multimedia → LLM extraction → graph → embeddings → communities."""

    def __init__(
        self,
        graph: GraphService | None = None,
        qdrant: QdrantService | None = None,
        meili: MeilisearchService | None = None,
        llm=None,
    ):
        self._graph = graph or graph_service
        self._qdrant = qdrant or qdrant_service
        self._meili = meili or meilisearch_service
        # Per-KB LLM override (chat/ingestion model) or the global service.
        self._llm = llm or llm_service
        # Per-KB concurrency / maintenance state (not process-global).
        # Default concurrency is 1 so in-process GGUF/HF models, Gemma extraction,
        # graph writes, and indexing do not overlap within a vault.
        self._process_semaphore = asyncio.Semaphore(
            settings.INGESTION_PIPELINE_CONCURRENCY
        )
        self._entity_locks = EntityLockManager()
        self._community_run_state_lock = threading.Lock()
        self._community_run_seq = 0
        self._community_run_active_seq = 0
        self._community_run_running = False
        self._temporal_digest_timer: threading.Timer | None = None
        self._temporal_digest_timer_lock = threading.Lock()
        self._temporal_digest_running = False

    async def process_note(self, note_input: NoteInput, note_id: str = None):
        """Run the full ingestion pipeline for a single note."""
        if not note_id:
            note_id = str(uuid.uuid4())

        # Register with the tracker BEFORE the semaphore so the community-detection
        # idle timer never fires while tasks are queued waiting for a slot.
        await _tracker.begin_ingestion(self.kb_id)
        await self._update_note_processing_status(
            note_id, "Queued for ingestion", None
        )

        # Wait for a pipeline slot.  Without this cap, sending 990 notes at once
        # spawns 990 concurrent coroutines that all hit the DB pool simultaneously.
        async with self._process_semaphore:
            await self._update_note_processing_status(
                note_id, "Starting ingestion", None
            )
            logger.info(
                f"\n{'='*70}\n"
                f"[Ingestion] START note_id={note_id}\n"
                f"  content_length={len(note_input.content or '')} chars\n"
                f"  title='{note_input.title or '(auto-generate)'}'\n"
                f"{'='*70}"
            )

            # Trigger the LangGraph Agent
            initial_state = {
                "input": note_input,
                "content": "",
                "extraction": None,
                "note_id": note_id,
                "created_at": None,
                "errors": [],
                "workflow": self,  # KB-specific instance so agent nodes write to the right stores
            }

            # Use ainvoke because the graph contains async nodes (multimodal_node)
            t_start = time.perf_counter()
            load_before = self._load_snapshot()
            try:
                final_state = await ingestion_agent.ainvoke(initial_state)
                t_end = time.perf_counter()
                self._log_timing(note_id, t_end - t_start, load_before, final_state)

                if final_state["errors"]:
                    logger.error(
                        f"[Ingestion] FAILURE note_id={note_id}: {final_state['errors']}"
                    )
                    raise RuntimeError(
                        f"Ingestion Agent Failed: {final_state['errors']}"
                    )

                extraction = final_state.get("extraction")
                if extraction:
                    logger.info(
                        f"[Ingestion] Agent complete — extracted "
                        f"{len(getattr(extraction, 'nodes', []))} nodes, "
                        f"{len(getattr(extraction, 'relationships', []))} relationships"
                    )
                    for n in getattr(extraction, "nodes", []):
                        logger.debug(f"  [Extraction] node: '{n.name}' type='{n.type}'")
                    for r in getattr(extraction, "relationships", []):
                        logger.debug(
                            f"  [Extraction] rel: '{r.source_name}' --[{r.relationship_type}]--> '{r.target_name}' "
                            f"(strength={r.strength}, confidence={r.confidence}, relevance={r.relevance})"
                        )

                # Mark as processed in SQLite metadata
                await self._mark_note_processed(note_id)
                await self._queue_leiden_recompute_if_due(note_id)

                duration = t_end - t_start
                logger.info(
                    f"\n{'='*70}\n"
                    f"[Ingestion] SUCCESS note_id={note_id} in {duration:.2f}s\n"
                    f"{'='*70}"
                )

                return {
                    "note_id": final_state["note_id"],
                    "extraction": final_state["extraction"].model_dump(),
                    "status": "success",
                    "processed_content": final_state["content"],
                }

            except Exception:
                await self._update_note_processing_status(
                    note_id, "Ingestion failed", None
                )
                await self._mark_note_failed(note_id)
                raise

            finally:
                # Always decrement the active counter and potentially schedule
                # community recompute, regardless of success or failure.
                await _tracker.end_ingestion(
                    self.rebuild_leiden_communities, kb_id=self.kb_id
                )
                # Models stay resident after a note: the idle watcher
                # (ORB_MODEL_IDLE_SECONDS, default 5 min) unloads them, and
                # loading any other model evicts them anyway. Unloading here
                # made every single-note ingest re-read multi-GB GGUFs.

    @staticmethod
    def _load_snapshot() -> dict:
        try:
            from app.services.local_models import model_load_clock

            return model_load_clock.snapshot()
        except Exception:  # pylint: disable=broad-exception-caught
            return {}

    @staticmethod
    def _log_timing(note_id: str, total: float, load_before: dict, final_state: dict) -> None:
        """One line per note: wall time split into model loads vs. everything else,
        plus per-stage seconds — so a slow disk and a slow model stop looking alike."""
        try:
            from app.services.local_models import ModelLoadClock, model_load_clock

            delta = model_load_clock.diff(load_before, model_load_clock.snapshot())
            loads = ModelLoadClock.describe(delta)
        except Exception:  # pylint: disable=broad-exception-caught
            delta, loads = {"total_seconds": 0.0}, "none"
        load = float(delta.get("total_seconds") or 0.0)
        stages = final_state.get("timings") or {}
        stage_text = " ".join(
            f"{k}={float(v):.1f}"
            for k, v in stages.items()
            if k != "extraction_chunks"
        )
        chunks = stages.get("extraction_chunks")
        logger.info(
            "[Timing] ingest note_id=%s total=%.1fs model_load=%.1fs inference=%.1fs "
            "loads=%s | %s%s",
            note_id,
            total,
            load,
            max(0.0, total - load),
            loads,
            stage_text,
            f" chunks={int(chunks)}" if chunks else "",
        )

    async def _update_note_fields(self, note_id: str, log_message: str, **values) -> None:
        """Single UPDATE helper behind all note-metadata status writes."""
        from sqlalchemy import update

        from app.core.database import AsyncSessionLocal
        from app.models.note import Note

        async with AsyncSessionLocal() as session:
            try:
                await session.execute(
                    update(Note).where(Note.id == note_id).values(**values)
                )
                await session.commit()
                if log_message:
                    logger.info(log_message)
            except Exception as e:
                logger.error(f"Error updating note {note_id} fields {values}: {e}")
                raise

    async def _update_note_processing_status(
        self, note_id: str, stage: str | None, model: str | None = None
    ) -> None:
        """Persist a user-facing ingestion stage/model for status polling."""
        await self._update_note_fields(
            note_id, "", processing_stage=stage, processing_model=model
        )

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _persist_note_body(self, note_id: str, content: str):
        """Persist enriched note body to the vault ``.md`` (source of truth)."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.note import Note
        from app.services.kb_registry import kb_registry
        from app.services.note_files import persist_note_body

        async with AsyncSessionLocal() as session:
            try:
                result = await session.execute(select(Note).where(Note.id == note_id))
                note = result.scalar_one_or_none()
                if note is None:
                    raise ValueError(f"Note {note_id} not found")

                kb = kb_registry.get_kb(note.kb_id) if note.kb_id else None
                if not kb or not kb.vault_path:
                    raise RuntimeError(
                        f"Note {note_id} has no vault — cannot persist body "
                        "(note bodies live in vault .md, not SQLite)"
                    )
                persist_note_body(note, kb, content)
                logger.info(
                    f"[Ingestion] Updated vault content for Note {note_id} "
                    f"({note.rel_path})"
                )
                await session.commit()
            except Exception as e:
                logger.error(f"Error updating note content: {e}")
                raise e  # Re-raise for tenacity

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _update_note_title(self, note_id: str, title: str):
        """Update the note title in SQLite metadata."""
        await self._update_note_fields(
            note_id,
            f"[Ingestion] Updated title for Note {note_id}: '{title}'",
            title=title,
        )

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _mark_note_processed(self, note_id: str):
        """Set processed=True in SQLite metadata to prevent re-runs."""
        await self._update_note_fields(
            note_id,
            f"[Ingestion] Marked Note {note_id} as Processed.",
            processed=True,
            failed=False,
            processing_stage="Ingestion complete",
            processing_model=None,
        )

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _mark_note_failed(self, note_id: str):
        """Set failed=True in SQLite so callers can distinguish permanent failure."""
        await self._update_note_fields(
            note_id,
            f"[Ingestion] Marked Note {note_id} as Failed.",
            processed=False,
            failed=True,
            processing_stage="Ingestion failed",
            processing_model=None,
        )

    def _write_ontology(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self,
        note_id: str,
        content: str,
        extraction: Extraction,
        _created_at: str,
        custom_title: str = None,
    ):
        logger.info(f"[Ontology] Writing ontology for note {note_id}")
        # 0. Resolve title: user-provided > extracted by LLM during extraction > separate LLM call
        if custom_title:
            title = custom_title
            logger.info(f"[Ontology] Using provided title: '{title}'")
        elif extraction.title:
            title = extraction.title
            logger.info(f"[Ontology] Using extracted title: '{title}'")
        else:
            title = self._llm.generate_title(
                content,
                model=self._llm.get_ingestion_model(),
            )
            logger.info(f"[Ontology] Generated title: '{title}'")
        # Base Note Node — structural node in Kuzu with kind='note'.
        # Always set name so "Mentioned in notes" can show the real title.
        query_note = """
        MERGE (n:Node {id: $id})
        ON CREATE SET n.kind = 'note', n.name = $title
        ON MATCH SET n.kind = 'note', n.name = $title
        """
        note_title = (title or "").strip() or "Untitled"
        self._graph.execute_query(
            query_note,
            {"id": note_id, "title": note_title},
        )

        # Helper to normalize names: strip # prefix, extra whitespace, and lowercase
        def normalize_name(name: str) -> str:
            if not name:
                return ""
            return name.lstrip("#").strip().lower()

        # 1. NODES (Batch — unified single type)
        name_to_id: dict[str, str] = (
            {}
        )  # populated below; declared here so it's always bound
        if extraction.nodes:
            # Build deduplicated (norm_name, text) pairs first, then embed in one shot.
            _embed_keys: list[str] = []
            _embed_texts: list[str] = []
            _seen_embed: set[str] = set()
            for n in extraction.nodes:
                norm_name = normalize_name(n.name)
                if not norm_name or norm_name in _seen_embed:
                    continue
                _seen_embed.add(norm_name)
                _embed_keys.append(norm_name)
                _embed_texts.append(
                    f"{norm_name} ({n.type}): {(n.isolated_context or '')}"
                )

            # Documents are embedded without any instruction prefix — the query↔document
            # asymmetry (query gets "Instruct: …\nQuery: " prefix, documents do not)
            # is what makes Qwen3-Embedding retrieval work correctly.
            _vectors = (
                embedding_service.embed_documents(_embed_texts) if _embed_texts else []
            )
            node_embeddings: dict[str, list[float]] = dict(zip(_embed_keys, _vectors))

            # Resolve or assign stable IDs: batch-look up Qdrant for ALL unique names
            # in a single query, then assign fresh UUIDs to whichever aren't found.
            # seen_in_batch deduplicates within this extraction — the same entity
            # name can appear more than once in a single note's extraction result,
            # and Qdrant won't have it yet for the first occurrence, so without this
            # both occurrences would mint independent UUIDs.
            _unique_names: list[str] = list(
                dict.fromkeys(
                    normalize_name(n.name)
                    for n in extraction.nodes
                    if normalize_name(n.name)
                )
            )
            _batch_id_map: dict[str, str | None] = (
                self._qdrant.find_node_ids_by_names(_unique_names)
                if _unique_names
                else {}
            )

            # Kuzu fallback: for any name Qdrant didn't find, check Kuzu directly.
            # This prevents minting a fresh UUID when a structural node already
            # exists from a previous ingestion run whose Qdrant write failed.
            _qdrant_misses = [n for n, v in _batch_id_map.items() if v is None]
            if _qdrant_misses:
                _kuzu_fallback = self._graph.find_nodes_by_exact_names(_qdrant_misses)
                if _kuzu_fallback:
                    for _fname, _fid in _kuzu_fallback.items():
                        _batch_id_map[_fname] = _fid
                    logger.debug(
                        f"[Ontology] Kuzu fallback resolved {len(_kuzu_fallback)} "
                        f"name(s) missing from Qdrant: {list(_kuzu_fallback.keys())}"
                    )

            node_data = []
            seen_in_batch: dict[str, str] = {}  # norm_name → assigned_id
            for node in extraction.nodes:
                norm_name = normalize_name(node.name)
                if not norm_name:
                    continue
                if norm_name in seen_in_batch:
                    # Same entity appeared twice in this extraction — reuse the
                    # first-assigned ID and skip appending a duplicate entry.
                    logger.debug(
                        f"  [Ontology] node '{norm_name}' — duplicate within batch, "
                        f"reusing id={seen_in_batch[norm_name]}"
                    )
                    continue
                existing_id = _batch_id_map.get(norm_name)
                is_new = existing_id is None
                assigned_id = existing_id or f"node_{str(uuid.uuid4())}"
                seen_in_batch[norm_name] = assigned_id
                node_type = ((node.type or "").strip().lower()) or "thing"
                node_data.append(
                    {
                        "id": assigned_id,
                        "name": norm_name,
                        "type": node_type,
                        "embedding": node_embeddings.get(norm_name),
                        "is_new": is_new,
                    }
                )
                logger.info(
                    f"  [Ontology] node '{norm_name}' type='{node_type}' "
                    f"id={assigned_id} ({'NEW' if is_new else 'EXISTING'})"
                )

            # Build name→ID map for relationship creation below
            name_to_id: dict[str, str] = {d["name"]: d["id"] for d in node_data}
            _new_count = sum(1 for d in node_data if d["is_new"])
            logger.info(
                f"[Ontology] {len(node_data)} nodes resolved ({_new_count} new) — writing to Kuzu"
            )

            # Write structural nodes to Kuzu with id + name + type so later
            # summarization can resolve by name if Qdrant stubs are missing.
            query_nodes = """
            MERGE (note:Node {id: $note_id})
            ON CREATE SET note.kind = 'note', note.name = $note_title
            ON MATCH SET note.kind = 'note', note.name = $note_title
            WITH note
            UNWIND $data AS item
            MERGE (n:Node {id: item.id}) ON CREATE SET n.kind = 'indexable'
            SET n.name = item.name, n.type = item.type
            MERGE (note)-[r:REFERENCES]->(n)
            SET r.note_id = $note_id
            """

            if node_data:
                self._graph.execute_query(
                    query_nodes,
                    {
                        "data": [
                            {
                                "id": d["id"],
                                "name": d["name"],
                                "type": d["type"] or "unknown",
                            }
                            for d in node_data
                        ],
                        "note_id": note_id,
                        "note_title": note_title,
                    },
                )

                # Seed Qdrant node_cores stubs for NEW nodes so that
                # _update_node_summary (running immediately after) finds the
                # correct ID via find_node_id_by_name instead of minting a
                # second different ID.  The stub is overwritten moments later
                # by _update_node_summary with the real description + embedding.
                # All stubs go up in a single batched upsert.
                _stub_cores = [
                    {
                        "node_id": d["id"],
                        "name": d["name"],
                        "node_type": d["type"],
                        "description_vector": d["embedding"],
                    }
                    for d in node_data
                    if d["is_new"] and d["embedding"]
                ]
                stub_ok = self._qdrant.upsert_node_cores(_stub_cores)
                logger.info(
                    "[Ontology] Seeded Qdrant stubs: %d point(s) %s",
                    len(_stub_cores),
                    "stored" if stub_ok else "FAILED",
                )
                if not stub_ok:
                    # The old message guessed at two causes and named neither
                    # the real one; a batch is all-or-nothing, so "fail=N" also
                    # implied N separate failures rather than one bad request.
                    detail = (
                        getattr(self._qdrant, "_last_upsert_error", None)
                        or "no further detail from Qdrant"
                    )
                    raise RuntimeError(
                        f"Failed to seed {len(_stub_cores)} Qdrant node_cores "
                        f"stub(s) — aborting ingest to avoid Kuzu/Qdrant ID "
                        f"split-brain. Qdrant said: {detail}"
                    )

        # 6. RELATIONSHIPS (New - Inter-node connections)
        if extraction.relationships:
            logger.info(
                f"[Ingestion] Creating {len(extraction.relationships)} relationships..."
            )

            # All nodes are :Indexable — no type mapping needed

            # Pre-collect all relationship endpoint names that aren't already in
            # name_to_id (i.e. they reference nodes from previous ingestion runs)
            # and batch-resolve them in one Qdrant query instead of one per rel.
            _rel_missing_names: set[str] = set()
            for _rel in extraction.relationships:
                if not _rel.source_name or not _rel.target_name:
                    continue
                _sn = _rel.source_name.lower().strip()
                _tn = _rel.target_name.lower().strip()
                if _sn and _sn not in name_to_id:
                    _rel_missing_names.add(_sn)
                if _tn and _tn not in name_to_id:
                    _rel_missing_names.add(_tn)
            _rel_fallback_ids: dict[str, str | None] = (
                self._qdrant.find_node_ids_by_names(list(_rel_missing_names))
                if _rel_missing_names
                else {}
            )
            logger.debug(
                f"[Relationship] Batch-resolved {len(_rel_missing_names)} fallback name(s) "
                f"({sum(1 for v in _rel_fallback_ids.values() if v)} found)"
            )

            # Collect all graph results first; batch-embed the new/evolved NL texts afterwards.
            _qdrant_rel_pending: list[tuple[dict, str, str, str]] = []
            _rel_errors: list[str] = []

            # Diagnostics counters for end-of-note summary log.
            _rel_total = len(extraction.relationships)
            _rel_written = 0
            _rel_skip_no_name = 0
            _rel_skip_no_source = 0
            _rel_skip_no_target = 0
            for rel in extraction.relationships:
                try:
                    # Validate required fields
                    if not rel.source_name or not rel.target_name:
                        logger.warning(
                            f"[Relationship] Skipping - missing source or target name: "
                            f"{rel.source_name} -> {rel.target_name}"
                        )
                        _rel_skip_no_name += 1
                        continue

                    # Default to "relates_to" if no relationship type provided
                    rel_type = (
                        rel.relationship_type.strip() if rel.relationship_type else ""
                    )
                    if not rel_type:
                        rel_type = "relates_to"
                        logger.warning(
                            f"[Relationship] No type provided for {rel.source_name} -> {rel.target_name}, "
                            f"defaulting to 'relates_to'"
                        )

                    # Strip entity name tokens from the predicate.
                    # LLMs sometimes embed the object into the verb, e.g.
                    # "plays_corliss_archer" → should be just "plays".
                    cleaned_rel_type = clean_rel_type(
                        rel_type, rel.source_name, rel.target_name
                    )
                    if cleaned_rel_type != rel_type:
                        logger.debug(
                            f"[Relationship] Predicate cleaned: '{rel_type}' → '{cleaned_rel_type}' "
                            f"({rel.source_name} → {rel.target_name})"
                        )
                        rel_type = cleaned_rel_type

                    source_label = "Indexable"
                    target_label = "Indexable"

                    # Normalize names to lowercase to match node storage
                    source_name_normalized = rel.source_name.lower().strip()
                    target_name_normalized = rel.target_name.lower().strip()

                    # Look up IDs from the name_to_id map (built from node_data above)
                    # falling back to the pre-batched Qdrant results for cross-run nodes.
                    src_node_id = (
                        name_to_id.get(source_name_normalized)
                        or _rel_fallback_ids.get(source_name_normalized)
                        or ""
                    )
                    tgt_node_id = (
                        name_to_id.get(target_name_normalized)
                        or _rel_fallback_ids.get(target_name_normalized)
                        or ""
                    )

                    if not src_node_id:
                        logger.warning(
                            f"  [Ontology] Relationship skipped — source '{source_name_normalized}' "
                            f"not found in name_to_id or Qdrant"
                        )
                        _rel_skip_no_source += 1
                        continue
                    if not tgt_node_id:
                        logger.warning(
                            f"  [Ontology] Relationship skipped — target '{target_name_normalized}' "
                            f"not found in name_to_id or Qdrant"
                        )
                        _rel_skip_no_target += 1
                        continue

                    logger.debug(
                        f"  [Ontology] Creating rel: '{source_name_normalized}' "
                        f"--[{rel_type}]--> '{target_name_normalized}' "
                        f"(strength={rel.strength}, confidence={rel.confidence}, relevance={rel.relevance}) "
                        f"NL: '{(rel.natural_language or '')}'"
                    )

                    # Create or update relationship
                    result = self._graph.create_or_update_relationship(
                        source_name=source_name_normalized,
                        source_label=source_label,
                        target_name=target_name_normalized,
                        target_label=target_label,
                        relationship_type=rel_type,
                        confidence=rel.confidence,
                        strength=rel.strength,
                        relevance=rel.relevance,
                        natural_language=(rel.natural_language or "").replace("_", " "),
                        note_id=note_id,
                        source_id=src_node_id,
                        target_id=tgt_node_id,
                    )

                    # Queue new/evolved rels for batch Qdrant embedding below.
                    if (
                        result.get("action") in ("created", "evolved")
                        and src_node_id
                        and tgt_node_id
                    ):
                        # Fall back to just the predicate (not a full sentence)
                        # so the stored NL never contains entity names that
                        # would be doubled when _build_node_text wraps it with
                        # "{name} {nl} {neighbour}".
                        nl_text = result.get("natural_language") or rel_type.replace(
                            "_", " "
                        )
                        # Ensure natural language never contains underscores.
                        nl_text = nl_text.replace("_", " ")
                        _qdrant_rel_pending.append(
                            (result, nl_text, src_node_id, tgt_node_id)
                        )

                    logger.info(
                        f"  [Ontology] Rel {result['action'].upper()}: "
                        f"'{source_name_normalized}' --[{rel_type}]--> '{target_name_normalized}'"
                    )
                    _rel_written += 1

                except Exception as e:  # pylint: disable=broad-exception-caught
                    err = (
                        f"{rel.source_name}->{rel.target_name}: {e}"
                    )
                    logger.error(f"[Relationship] Failed to create relationship {err}")
                    _rel_errors.append(err)

            if _rel_errors:
                preview = "; ".join(_rel_errors[:5])
                more = (
                    f" (+{len(_rel_errors) - 5} more)" if len(_rel_errors) > 5 else ""
                )
                raise RuntimeError(
                    f"Failed to write {len(_rel_errors)} relationship(s): "
                    f"{preview}{more}"
                )

            # Batch-embed all new/evolved relationship NL texts in one round-trip,
            # then write all points in a single batched Qdrant upsert.
            if _qdrant_rel_pending:
                _nl_batch = [item[1] for item in _qdrant_rel_pending]
                _nl_vectors = embedding_service.embed_documents(_nl_batch)
                self._qdrant.upsert_node_relationships(
                    [
                        {
                            "relationship_id": result["relationship_id"],
                            "natural_language": nl_text,
                            "nl_vector": nl_vector,
                            "source_node_id": src_node_id,
                            "target_node_id": tgt_node_id,
                        }
                        for (result, nl_text, src_node_id, tgt_node_id), nl_vector in zip(
                            _qdrant_rel_pending, _nl_vectors
                        )
                    ]
                )
                logger.debug(
                    f"  [Ontology] Qdrant rels written: {len(_qdrant_rel_pending)}"
                )

            # Emit a compact per-note relationship summary for observability.
            _rel_skip_total = (
                _rel_skip_no_name + _rel_skip_no_source + _rel_skip_no_target
            )
            logger.info(
                f"[Relationship] note_id={note_id} total={_rel_total} "
                f"written={_rel_written} skipped={_rel_skip_total} "
                f"(no_name={_rel_skip_no_name} "
                f"no_source={_rel_skip_no_source} "
                f"no_target={_rel_skip_no_target})"
            )

        return title

    async def _queue_leiden_recompute_if_due(self, note_id: str) -> None:
        """Queue community detection and/or schedule a temporal digest rebuild after ingestion.

        Both features gate on their respective config switches independently so that
        disabling one does not suppress the other.
        """
        from app.core.config import settings as _settings

        # ── Community detection ───────────────────────────────────────────────
        if _settings.COMMUNITY_DETECTION_ENABLED:
            rows = self._graph.execute_query(
                """
                MATCH (:Node {id: $note_id})-[*1]-(n:Node)
                WHERE n.kind = 'indexable' AND n.id IS NOT NULL
                RETURN DISTINCT n.id AS node_id
                """,
                {"note_id": note_id},
            )
            node_ids = [row["node_id"] for row in rows if row.get("node_id")]
            if node_ids:
                _, queue_size = await _tracker.queue_nodes_for_community_recompute(
                    node_ids, kb_id=self.kb_id
                )
                logger.info(
                    f"[Community] Queued {len(node_ids)} node IDs for Leiden recompute "
                    f"(queue size: {queue_size}) — IDs: {node_ids}{'...' if len(node_ids) > 10 else ''}"
                )

        # ── Temporal digests (debounced) ──────────────────────────────────────
        # Restart a module-level timer on every ingestion.  The rebuild only
        # fires after _TEMPORAL_DIGEST_IDLE_SECONDS of inactivity, so a burst
        # of notes produces exactly one rebuild once the system goes quiet.
        if _settings.TEMPORAL_DIGESTS_ENABLED:
            with self._temporal_digest_timer_lock:
                if self._temporal_digest_timer is not None:
                    self._temporal_digest_timer.cancel()
                self._temporal_digest_timer = threading.Timer(
                    COMMUNITY_IDLE_SECONDS,
                    self.build_temporal_digests,
                )
                self._temporal_digest_timer.daemon = True
                self._temporal_digest_timer.start()
            logger.info(
                f"[TemporalDigest] Digest rebuild scheduled "
                f"({COMMUNITY_IDLE_SECONDS} s idle window)."
            )

    async def _update_neighborhoods(
        self, nodes, new_content: str, note_created_at: str | None = None
    ):
        """
        Refreshes isolated contexts for all nodes affected by this note.
        Runs with concurrency=4 and uses entity-level locks to prevent races
        when multiple ingestion runs touch the same node.

        Groups by node name so each unique node is processed once per ingestion run.
        No description/facts/questions are generated here — only isolated contexts
        are accumulated and embedded.
        """
        # Group unique contexts per node name.
        name_to_contexts: dict[str, list[str]] = {}
        name_to_type: dict[str, str] = {}
        name_ctx_seen: dict[str, set[str]] = {}

        for node in nodes or []:
            name = (node.name or "").lstrip("#").strip().lower()
            if not name:
                continue
            context = getattr(node, "isolated_context", "") or new_content
            ntype = (getattr(node, "type", "") or "").lower().strip() or "thing"
            ctx_key = (context or "").strip()
            if name not in name_to_contexts:
                name_to_contexts[name] = []
                name_to_type[name] = ntype
                name_ctx_seen[name] = set()
            if ctx_key and ctx_key not in name_ctx_seen[name]:
                name_ctx_seen[name].add(ctx_key)
                name_to_contexts[name].append(context)

        nodes_to_update = [
            (name, ctxs, name_to_type[name]) for name, ctxs in name_to_contexts.items()
        ]

        if nodes_to_update:
            _sem = asyncio.Semaphore(4)

            async def _run_summary(name, new_contexts, ntype):
                async with _sem:
                    await self._update_node_summary(
                        "Indexable",
                        name,
                        new_contexts,
                        node_type=ntype,
                        note_created_at=note_created_at,
                    )

            logger.info(
                f"[Neighborhood] Updating {len(nodes_to_update)} unique node summaries "
                f"(concurrency=4)…"
            )
            await asyncio.gather(
                *[_run_summary(n, c, t) for n, c, t in nodes_to_update]
            )
            logger.info(
                f"[Neighborhood] {len(nodes_to_update)} node context updates complete."
            )

    async def _update_node_summary(  # pylint: disable=too-many-locals,too-many-statements,too-many-arguments,too-many-positional-arguments
        self,
        label: str,
        name: str,
        new_contexts: list[str],
        node_type: str = "",
        note_created_at: str | None = None,
    ):
        """
        Updates a node by accumulating isolated contexts only.

        This path intentionally does NOT generate description/facts/questions.
        Ingestion stores verbatim isolated contexts and their embeddings, and keeps
        structural nodes + relationships in the graph.

        Args:
            label: Node label (Entity, Concept, Task, etc.)
            name: Node identifier
            new_contexts: Contexts extracted for this node in the current ingestion run
        """
        async with self._entity_locks.get_lock(label, name):
            # 1. Resolve node_id. Qdrant is the primary lookup, but if it misses
            # while Kuzu already has a structural node with this name, reuse that
            # existing graph ID to avoid minting duplicate same-name nodes.
            def _get_existing():
                return self._qdrant.find_node_id_by_name(name)

            node_id: str | None = await asyncio.to_thread(_get_existing)
            existing_contexts: list[str] = []
            # Full Qdrant content for this node (when it exists) — fetched once and
            # reused below for type resolution instead of re-querying Qdrant.
            _core_content: dict = {}

            if node_id:
                # Fetch existing isolated_contexts from Qdrant
                def _get_qdrant_contexts():
                    _content = self._qdrant.get_node_content_by_id(node_id)
                    return _content or {}

                _existing_content = await asyncio.to_thread(_get_qdrant_contexts)
                _core_content = _existing_content
                existing_contexts = _existing_content.get("isolated_contexts", [])
                logger.info(
                    f"  [NodeSummary] '{name}' EXISTING id={node_id} "
                    f"— {len(existing_contexts)} prior context(s) fetched from Qdrant"
                )
            else:

                def _find_existing_graph_id():
                    _matches = self._graph.find_nodes_by_name([name], fuzzy=False)
                    _candidates = [
                        m.get("node_id")
                        for m in _matches
                        if (
                            m.get("node_id")
                            and (m.get("labels") or [""])[0] == "indexable"
                        )
                    ]
                    if not _candidates:
                        return None
                    return sorted(_candidates)[0]

                _graph_existing_id = await asyncio.to_thread(_find_existing_graph_id)
                if _graph_existing_id:
                    node_id = _graph_existing_id
                    logger.warning(
                        f"  [NodeSummary] '{name}' missing in Qdrant node_cores but present in Kuzu "
                        f"(id={node_id}) — reusing existing graph ID to prevent duplicate node creation"
                    )

                    # Fetch any existing isolated_contexts from Qdrant (the node may
                    # have contexts in node_isolated_contexts even without a node_cores
                    # entry). Without this, existing_contexts stays [] and the
                    # subsequent Meilisearch upsert would silently wipe stored contexts.
                    def _get_kuzu_node_content():
                        return self._qdrant.get_node_content_by_id(node_id) or {}

                    _core_content = await asyncio.to_thread(_get_kuzu_node_content)
                    existing_contexts = _core_content.get("isolated_contexts", [])
                    if existing_contexts:
                        logger.info(
                            f"  [NodeSummary] '{name}' fetched {len(existing_contexts)} "
                            f"prior context(s) via Kuzu ID fallback"
                        )
                else:
                    # Truly new node — mint an ID and ensure structural Kuzu node exists
                    node_id = f"node_{str(uuid.uuid4())}"
                    logger.info(
                        f"  [NodeSummary] '{name}' NEW id={node_id} — minting and writing structural Kuzu node"
                    )

                    def _merge_node():
                        self._graph.execute_query(
                            "MERGE (n:Node {id: $node_id}) ON CREATE SET n.kind = 'indexable'"
                            " SET n.name = $name, n.type = $type",
                            {
                                "node_id": node_id,
                                "name": name,
                                "type": node_type or "unknown",
                            },
                        )

                    await asyncio.to_thread(_merge_node)

            # 2. Append all new contexts that aren't already stored (dedup against existing).
            # Collecting all of them before the LLM call means one summary generation
            # per ingestion run regardless of how many contexts this node received.
            _existing_stripped = {c.strip() for c in existing_contexts if c}
            _contexts_to_add: list[str] = []
            for _nc in new_contexts or []:
                _nc_stripped = _nc.strip() if _nc else ""
                if _nc_stripped and _nc_stripped not in _existing_stripped:
                    existing_contexts.append(_nc)
                    _existing_stripped.add(_nc_stripped)
                    _contexts_to_add.append(_nc)
            logger.debug(
                f"  [NodeSummary] '{name}' context count after append: {len(existing_contexts)} "
                f"({len(_contexts_to_add)} new, {len(new_contexts or []) - len(_contexts_to_add)} duplicate(s) skipped)"
            )

            # 3. Resolve semantic node type from the content fetched in step 1
            # (or fall back to caller-supplied type) — no extra Qdrant round trip.
            node_type = (
                _core_content.get("type")
                or (node_type or "thing").lower().strip()
                or "thing"
            )

            logger.info(
                f"  [NodeSummary] '{name}' generating embeddings for "
                f"{len(_contexts_to_add)} new isolated context(s)"
            )

            # 4. Generate embeddings for Qdrant writes.
            # New contexts and the merged-context passage (used for node_cores in
            # step 6b) are embedded in a single batched call.
            merged_ctx_text = " ".join(c for c in existing_contexts if c and c.strip())

            def _generate_embeddings():
                # Embed only new isolated contexts (existing are already persisted).
                _new_ctx_texts = [c for c in _contexts_to_add if c and c.strip()]
                _batch = list(_new_ctx_texts)
                if merged_ctx_text:
                    _batch.append(merged_ctx_text)
                vectors = embedding_service.embed_documents(_batch) if _batch else []

                new_ctx_pairs: list[tuple[str, list[float]]] = list(
                    zip(_new_ctx_texts, vectors[: len(_new_ctx_texts)])
                )
                _merged_vec = vectors[len(_new_ctx_texts)] if merged_ctx_text else None
                return new_ctx_pairs, _merged_vec

            new_ctx_pairs, merged_ctx_vector = await asyncio.to_thread(
                _generate_embeddings
            )
            logger.debug(
                f"  [NodeSummary] '{name}' embeddings generated: "
                f"new_ctx={len(new_ctx_pairs)}"
            )

            # 5. Ensure the Kuzu structural node exists with this ID and carry
            # name + type so the graph endpoint never needs Qdrant for display labels.
            def _save_update():
                self._graph.execute_query(
                    "MERGE (n:Node {id: $node_id}) ON CREATE SET n.kind = 'indexable'"
                    " SET n.name = $name, n.type = $type",
                    {"node_id": node_id, "name": name, "type": node_type or "unknown"},
                )

            await asyncio.to_thread(_save_update)
            logger.debug(f"  [NodeSummary] '{name}' Kuzu structural MERGE done")

            # 6. Write to Qdrant (append new contexts).
            def _write_qdrant():
                # Append each new context — existing ones stay in Qdrant untouched
                wrote = 0
                for _ctx_text, _ctx_vector in new_ctx_pairs:
                    if self._qdrant.append_node_item(
                        collection_name=self._qdrant.col_contexts,
                        node_id=node_id,
                        content=_ctx_text,
                        vector=_ctx_vector,
                        note_created_at=note_created_at,
                    ):
                        wrote += 1
                return wrote

            wrote_count = await asyncio.to_thread(_write_qdrant)
            expected = len(new_ctx_pairs)
            if expected:
                if wrote_count == expected:
                    logger.info(
                        f"  [NodeSummary] '{name}' Qdrant contexts stored — "
                        f"ctx_appended({wrote_count}/{expected})"
                    )
                else:
                    logger.error(
                        f"  [NodeSummary] '{name}' Qdrant context write incomplete — "
                        f"ctx_appended({wrote_count}/{expected})"
                    )
                    raise RuntimeError(
                        f"Failed to persist contexts to Qdrant for '{name}' "
                        f"({wrote_count}/{expected}). Check Qdrant availability and "
                        f"embedding dimensions."
                    )

            # 6b. Write merged-context vector to node_cores.
            # Joining all accumulated contexts into a single passage and embedding
            # it lets vector search match multi-constraint queries against the full
            # node content at once, rather than scoring each sentence in isolation.
            # The vector was already computed in the step-4 batch embed call.
            if merged_ctx_text and merged_ctx_vector:

                def _write_node_core():
                    return self._qdrant.upsert_node_core(
                        node_id=node_id,
                        name=name,
                        node_type=node_type,
                        description_vector=merged_ctx_vector,
                        description=merged_ctx_text,
                    )

                core_ok = await asyncio.to_thread(_write_node_core)
                if not core_ok:
                    raise RuntimeError(
                        f"Failed to upsert Qdrant node_cores for '{name}' "
                        f"(id={node_id}). Check Qdrant and embedding dimensions."
                    )
                logger.debug(
                    f"  [NodeSummary] '{name}' node_cores merged vector written "
                    f"({len(existing_contexts)} context(s) merged)"
                )

            # 7. Write to Meilisearch only after Qdrant succeeded (Qdrant is SoT).
            def _write_meili():
                # Fetch relationship NL sentences from Qdrant (not Kuzu)
                rel_qdrant = self._qdrant.get_relationships_for_node_ids([node_id])
                rel_nl = " ".join(
                    r.get("natural_language", "")
                    for r in rel_qdrant
                    if r.get("natural_language")
                )
                contexts_text = " ".join(
                    ctx for ctx in existing_contexts if ctx and ctx.strip()
                )
                # Defense-in-depth: if we somehow ended up with no contexts, fetch
                # what Meilisearch already has so we don't wipe it with a blank upsert.
                if not contexts_text:
                    try:
                        existing_meili = self._meili.get_node(node_id) or {}
                        contexts_text = existing_meili.get("isolated_contexts", "")
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                self._meili.index_node(
                    node_id=node_id,
                    name=name,
                    node_type=node_type,
                    isolated_contexts_text=contexts_text,
                    relationship_natural_language=rel_nl,
                )

            await asyncio.to_thread(_write_meili)
            logger.info(f"  [NodeSummary] '{name}' Meilisearch index complete")

            logger.info(
                f"  [NodeSummary] ✓ COMPLETE: '{name}' (type='{node_type}', id={node_id})"
            )

    @staticmethod
    def _parse_name_summary(raw: str) -> tuple[str | None, str | None]:
        """Parse ``NAME: ...\nSUMMARY: ...`` LLM output into (name, summary)."""
        name: str | None = None
        summary_lines: list[str] = []
        in_summary = False
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("NAME:") and not in_summary:
                name = stripped.split(":", 1)[1].strip()
            elif stripped.upper().startswith("SUMMARY:"):
                in_summary = True
                first = stripped.split(":", 1)[1].strip()
                if first:
                    summary_lines.append(first)
            elif in_summary:
                summary_lines.append(stripped)
        summary = " ".join(s for s in summary_lines if s) or None
        return name, summary

    @staticmethod
    def _is_generic_community_name(  # pylint: disable=too-many-return-statements
        name: str | None,
    ) -> bool:
        """Heuristic guardrail for low-quality community names.

        Rejects template-like names that are not useful to end users.
        """
        if not name:
            return True

        normalized = re.sub(r"\s+", " ", name.strip()).lower()
        if not normalized:
            return True

        if re.fullmatch(r"community\s+l\d+[-\s]?\d+", normalized):
            return True
        if normalized.startswith("community "):
            return True

        banned_phrases = (
            "isolated conceptual node cluster",
            "isolated conceptual cluster",
            "isolated conceptual fragment",
            "isolated conceptual echo",
            "isolated conceptual echoes",
            "isolated conceptual anomaly",
            "isolated conceptual collection",
            "isolated conceptual core",
            "isolated node community",
            "isolated node cluster",
            "isolated core node cluster",
            "isolated single node community",
            "isolated concept",
            "potential anomaly",
            "initial state",
            "provisional",
            "minimal connection",
        )
        if any(phrase in normalized for phrase in banned_phrases):
            return True

        # Names made only of generic words are not user-friendly.
        generic_tokens = {
            "isolated",
            "conceptual",
            "node",
            "nodes",
            "cluster",
            "community",
            "core",
            "collection",
            "fragment",
            "echo",
            "echoes",
            "anomaly",
            "pair",
            "transient",
            # Additional filler words the LLM uses for empty/thin clusters:
            "temporal",
            "reflection",
            "reflections",
            "potential",
            "seeds",
            "seed",
            "observation",
            "observations",
            "silent",
            "silence",
            "unresolved",
            "statistical",
            "minimal",
            "interest",
            "inquiry",
            "shadow",
            "shadows",
            "whisper",
            "whispers",
            "remnant",
            "remnants",
            "trace",
            "traces",
            "fleeting",
            "ephemeral",
            "abstract",
            "nascent",
            "liminal",
        }
        words = [w for w in re.split(r"[^a-z0-9]+", normalized) if w]
        if words and all(w in generic_tokens for w in words):
            return True

        return False

    @staticmethod
    def _derive_fallback_community_name(member_rows: list[dict]) -> str:
        """Create a readable deterministic fallback from member names."""
        member_names = [row.get("name") for row in member_rows if row.get("name")]
        if not member_names:
            return "Related knowledge topics"
        if len(member_names) == 1:
            return f"About {member_names[0]}"
        if len(member_names) == 2:
            return f"{member_names[0]} and {member_names[1]}"
        return f"{member_names[0]} and related topics"

    @staticmethod
    def _format_member_context(rows: list[dict]) -> str:
        """Format member node rows into a bulleted context string for LLM prompts."""
        lines = []
        for row in rows:
            name = row.get("name", "")
            label = (row.get("labels") or ["Node"])[0]
            contexts = row.get("isolated_contexts") or []
            if contexts:
                context_text = " | ".join(contexts)
                lines.append(f"- {name} ({label}): {context_text}")
            else:
                lines.append(f"- {name} ({label})")
        return "\n".join(lines)

    def _build_community_summary(
        self,
        member_rows: list[dict],
        community_level: int,
        strict_naming: bool = False,
    ) -> tuple[str | None, str | None]:
        """Generate a community name and summary in a single LLM call.

        All member descriptions are passed at once — no chunking.  The LLM context
        window is large enough to handle even the biggest L2 communities (~220 nodes).
        L1 and L0 summaries should use ``_build_rollup_summary`` instead, which rolls
        up from child community summaries rather than raw node descriptions.
        """
        context = self._format_member_context(member_rows)
        prompt = (
            "The following entities are closely related based on how they appear across a knowledge graph.\n\n"
            f"Cluster level: {community_level}  [L2 = most fine-grained → L1 = mid-level → L0 = broadest]\n"
            f"Entities ({len(member_rows)} total):\n{context}\n\n"
            "Generate:\n"
            "1. A short descriptive name that captures what ties these entities together\n"
            "2. A thorough summary covering the key themes, notable connections, relationships, and any "
            "meaningful patterns or distinctions among these entities. Write as many sentences as needed.\n\n"
            "Name rules:\n"
            "- Use plain, natural language\n"
            "- Anchor the name in concrete topics drawn from the entities themselves\n"
            "- Do NOT use meta-labels like 'Node Cluster', 'Isolated Group', 'Cluster L2-14', or any variant\n"
            "- Do NOT use the words isolated / node / cluster / community / group as the central theme\n\n"
            "Reply in EXACTLY this format — no preamble, no trailing text:\n"
            "NAME: <name>\n"
            "SUMMARY: <summary>"
        )
        if strict_naming:
            prompt += (
                "\n\nThis is a retry because the previous name was too generic. "
                "The NAME must be specific and user-facing."
            )
        raw = (
            self._llm.reason(
                prompt,
                model=self._llm.get_ingestion_model(),
            )
            or ""
        )
        return self._parse_name_summary(raw)

    def _build_rollup_summary(
        self,
        child_community_rows: list[dict],
        community_level: int,
        strict_naming: bool = False,
    ) -> tuple[str | None, str | None]:
        """Generate a community name and summary by rolling up from child community summaries.

        Used for L1 (rolls up L2 descriptions) and L0 (rolls up L1 descriptions).
        All child summaries are passed in a single LLM call — no chunking.
        """
        if not child_community_rows:
            return None, None
        context = "\n".join(
            f"- {row['name']}: {row['summary']}"
            for row in child_community_rows
            if row.get("summary")
        )
        if not context:
            return None, None
        prompt = (
            f"The following are descriptions of {len(child_community_rows)} related groups "
            "that together form a broader connected topic.\n\n"
            f"Cluster level: {community_level}  [L2 = most fine-grained → L1 = mid-level → L0 = broadest]\n\n"
            "Groups:\n"
            f"{context}\n\n"
            "Synthesize:\n"
            "1. A short descriptive name capturing the overarching theme across all groups\n"
            "2. A thorough summary covering the shared themes, what connects the groups, major patterns, "
            "and the big-picture significance. Write as many sentences as needed.\n\n"
            "Name rules:\n"
            "- Use plain, natural language\n"
            "- Anchor the name in concrete topics drawn from the group descriptions\n"
            "- Do NOT use meta-labels like 'Node Cluster', 'Isolated Group', or any variant\n"
            "- Do NOT use the words isolated / node / cluster / community / group as the central theme\n\n"
            "Reply in EXACTLY this format — no preamble, no trailing text:\n"
            "NAME: <name>\n"
            "SUMMARY: <summary>"
        )
        if strict_naming:
            prompt += (
                "\n\nThis is a retry because the previous name was too generic. "
                "The NAME must be specific and user-facing."
            )
        raw = (
            self._llm.reason(
                prompt,
                model=self._llm.get_ingestion_model(),
            )
            or ""
        )
        return self._parse_name_summary(raw)

    def rebuild_leiden_communities(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self,
    ) -> int:
        """Run a full 3-level Leiden recomputation and rebuild community nodes.

        Cooperative cancellation: checks ``_tracker.cancel_recompute`` between every
        cluster summary.  When set, the run exits early (returning the count built so far) so a
        pending ingestion can proceed.  The tracker resets the flag and reschedules the full run
        after the next idle window.
        """

        # Register this as the newest requested run. If another run is active, request
        # cancellation and wait for handoff. Only the newest request is allowed to start.
        with self._community_run_state_lock:
            self._community_run_seq += 1
            requested_seq = self._community_run_seq
            had_active_run = self._community_run_running
            active_seq = self._community_run_active_seq
            if had_active_run:
                _tracker.cancel_recompute.set()

        if had_active_run:
            logger.info(
                "[Community] New recompute requested while run "
                f"#{active_seq} is active — signalling cancel and waiting for handoff."
            )

        while True:
            with self._community_run_state_lock:
                if requested_seq != self._community_run_seq:
                    logger.info(
                        "[Community] Recompute request superseded by a newer request "
                        f"(request #{requested_seq}) — skipping."
                    )
                    return 0
                if not self._community_run_running:
                    self._community_run_running = True
                    self._community_run_active_seq = requested_seq
                    break
            time.sleep(0.25)

        # Start with a clean cancellation state for this newly claimed run.
        _tracker.cancel_recompute.clear()
        # If ingestion became active during claim handoff, preserve the cancel signal.
        if _tracker.has_active_ingestions(self.kb_id):
            _tracker.cancel_recompute.set()

        try:
            logger.info(
                f"\n{'='*70}\n"
                f"[Community] Starting full Leiden community recompute\n"
                f"{'='*70}"
            )

            nodes = self._graph.get_indexable_nodes_for_communities()
            if not nodes:
                logger.warning(
                    "[Community] No indexable nodes found — aborting Leiden recompute."
                )
                return 0

            logger.info(f"[Community] {len(nodes)} indexable nodes fetched from Kuzu")

            # Enrich structural node rows with content from Qdrant (description, facts)
            _node_ids_for_leiden = [n["node_id"] for n in nodes if n.get("node_id")]
            if _node_ids_for_leiden:
                _qdrant_content_map = self._qdrant.get_nodes_content_by_ids(
                    _node_ids_for_leiden
                )
                for n in nodes:
                    _nid = n.get("node_id")
                    if _nid and _nid in _qdrant_content_map:
                        _c = _qdrant_content_map[_nid]
                        n["isolated_contexts"] = _c.get("isolated_contexts", [])
                _enriched = sum(1 for n in nodes if n.get("isolated_contexts"))
                logger.info(
                    f"[Community] Qdrant content enrichment: {_enriched}/{len(nodes)} nodes "
                    f"enriched with isolated contexts"
                )

            old_community_ids = self._graph.clear_all_communities()
            logger.info(
                f"[Community] Cleared {len(old_community_ids)} old community nodes "
                f"(Kuzu + Qdrant + Meilisearch)"
            )
            for old_community_id in old_community_ids:
                self._qdrant.delete_node(old_community_id)
                self._meili.delete_node(old_community_id)

            node_ids = [node["node_id"] for node in nodes]
            node_lookup = {node["node_id"]: node for node in nodes}

            # ─── True hierarchical community building ─────────────────────────────
            # All three levels use embedding similarity clustering (agglomerative,
            # cosine distance, average linkage) with progressively looser thresholds:
            #   L2 (finest):  distance_threshold=0.25  — only closely related entities
            #   L1 (mid):     distance_threshold=0.50  — moderate thematic overlap
            #   L0 (broadest):distance_threshold=0.75  — broad topic grouping
            # L2 clusters all entities. L1 clusters L2 communities. L0 clusters L1 communities.
            # ──────────────────────────────────────────────────────────────────────
            level_assignments: dict[str, dict[int, str]] = (
                {}
            )  # entity_node_id → {level → community_id}
            community_summaries: dict[str, str] = {}  # community_id → summary text
            community_names: dict[str, str] = {}  # community_id → display name
            community_entity_members: dict[str, list[str]] = (
                {}
            )  # community_id → entity node IDs
            created = 0

            # ── Local helpers ────────────────────────────────────────────────────

            def _embedding_cluster(
                item_ids: list[str],
                item_texts: list[str],
                distance_threshold: float = 0.35,
            ) -> list[list[str]]:
                """Cluster items by embedding cosine similarity.

                Uses agglomerative clustering with average linkage on cosine distance.
                distance_threshold=0.35 means items need cosine similarity ≥ ~0.65
                to be grouped together. No need to pre-specify cluster count.
                """
                import numpy as np
                from sklearn.cluster import AgglomerativeClustering

                if not item_ids:
                    return []
                if len(item_ids) == 1:
                    return [list(item_ids)]

                # Embed all texts in one batched call.
                vectors = embedding_service.embed_documents(item_texts)
                arr = np.array(vectors, dtype=np.float32)

                # L2-normalise so cosine distance = 1 − cosine_similarity.
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                arr /= norms

                clustering = AgglomerativeClustering(
                    n_clusters=None,
                    distance_threshold=distance_threshold,
                    metric="cosine",
                    linkage="average",
                )
                labels = clustering.fit_predict(arr)

                clusters: dict[int, list[str]] = {}
                for item_id, label in zip(item_ids, labels):
                    clusters.setdefault(int(label), []).append(item_id)
                return list(clusters.values())

            def _commit_community(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
                community_level: int,
                member_entity_ids: list[str],
                rollup_rows: list[dict],
                cluster_label: str,
            ) -> str | None:
                """Summarise, name, and persist one community node.

                ``rollup_rows`` is non-empty for L1/L0 (summaries of child communities +
                orphan descriptions).  Empty list triggers direct node-description
                summarisation (L2).  Returns the new community_id, or None if skipped.
                """
                nonlocal created

                if not member_entity_ids:
                    return None
                if len(member_entity_ids) == 1:
                    logger.debug(
                        f"[Community] Skipping {cluster_label}: singleton entity cluster "
                        f"({node_lookup.get(member_entity_ids[0], {}).get('name', member_entity_ids[0])})"
                    )
                    return None
                if rollup_rows and len(rollup_rows) == 1:
                    logger.debug(
                        f"[Community] Skipping {cluster_label}: only 1 rollup input "
                        f"('{rollup_rows[0].get('name', '?')}')"
                    )
                    return None

                member_rows = [
                    node_lookup[nid] for nid in member_entity_ids if nid in node_lookup
                ]
                name: str | None = None
                summary: str | None = None
                try:
                    if not rollup_rows:
                        name, summary = self._build_community_summary(
                            member_rows, community_level
                        )
                    else:
                        name, summary = self._build_rollup_summary(
                            rollup_rows, community_level
                        )
                except Exception as _summ_err:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        f"[Community] Summary failed for {cluster_label}: {_summ_err}"
                    )
                    return None

                # Reject generic names and retry up to 2 times.
                for retry_idx in range(2):
                    if name and not self._is_generic_community_name(name):
                        break
                    if _tracker.cancel_recompute.is_set():
                        return None
                    logger.warning(
                        f"[Community] {cluster_label}: rejecting generic name "
                        f"'{name or '(empty)'}' (retry {retry_idx + 1}/2)"
                    )
                    try:
                        if rollup_rows:
                            rn, rs = self._build_rollup_summary(
                                rollup_rows, community_level, strict_naming=True
                            )
                        else:
                            rn, rs = self._build_community_summary(
                                member_rows, community_level, strict_naming=True
                            )
                        if rn:
                            name = rn
                        if rs:
                            summary = rs
                    except (
                        Exception
                    ) as _retry_err:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            f"[Community] {cluster_label}: name retry failed: {_retry_err}"
                        )

                if not name:
                    name = self._derive_fallback_community_name(member_rows)
                elif self._is_generic_community_name(name):
                    logger.warning(
                        f"[Community] {cluster_label}: using fallback name after generic output '{name}'"
                    )
                    name = self._derive_fallback_community_name(member_rows)
                if not summary:
                    summary = (
                        f"Community at level {community_level} containing "
                        f"{len(member_entity_ids)} related nodes."
                    )

                community_id = f"community_l{community_level}_{uuid.uuid4().hex}"

                self._graph.create_leiden_community(
                    community_id=community_id,
                    community_level=community_level,
                    name=name,
                    summary=summary,
                    member_node_ids=member_entity_ids,
                )
                logger.debug(
                    f"  [Community] Kuzu community node created: '{name}' id={community_id}"
                )

                self._meili.index_node(
                    node_id=community_id,
                    name=name,
                    node_type="community",
                    community_level=community_level,
                )

                # Write membership NL sentences to Qdrant (flagged is_community_rel=True).
                try:
                    _nl_texts = [
                        f"{node_lookup.get(nid, {}).get('name') or nid} is a member of the '{name}' community."
                        for nid in member_entity_ids
                        if nid in node_lookup
                    ]
                    _nl_vectors = (
                        embedding_service.embed_documents(_nl_texts)
                        if _nl_texts
                        else []
                    )
                    _valid_nids = [
                        nid for nid in member_entity_ids if nid in node_lookup
                    ]
                    self._qdrant.upsert_node_relationships(
                        [
                            {
                                "relationship_id": f"community_rel_{community_id}_{nid}",
                                "natural_language": nl_text,
                                "nl_vector": nl_vec,
                                "source_node_id": nid,
                                "target_node_id": community_id,
                                "is_community_rel": True,
                            }
                            for nid, nl_text, nl_vec in zip(
                                _valid_nids, _nl_texts, _nl_vectors
                            )
                        ]
                    )
                    logger.debug(
                        f"  [Community] Qdrant NL sentences written: {len(_nl_texts)} "
                        f"for community '{name}'"
                    )
                except Exception as _rel_err:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        f"[Community] Membership NL write failed for {community_id}: {_rel_err}"
                    )

                logger.info(
                    f"  [Community] ✓ Created L{community_level} community '{name}' "
                    f"(id={community_id}, {len(member_entity_ids)} members)"
                )
                self._graph.set_node_community_membership(
                    member_entity_ids, community_id, community_level
                )
                for nid in member_entity_ids:
                    level_assignments.setdefault(nid, {})[
                        community_level
                    ] = community_id
                community_summaries[community_id] = summary
                community_names[community_id] = name
                community_entity_members[community_id] = list(member_entity_ids)
                created += 1
                return community_id

            # ─── Level 2: embedding cluster on raw entity nodes ────────────────────
            l2_entity_texts: list[str] = []
            for _nid in node_ids:
                _n = node_lookup[_nid]
                _ctxs = " | ".join(_n.get("isolated_contexts") or [])
                l2_entity_texts.append(
                    f"{_n.get('name') or _nid}: {_ctxs}"
                    if _ctxs
                    else (_n.get("name") or _nid)
                )
            logger.info(
                f"[Community] Embedding {len(node_ids)} entities for L2 similarity clustering"
            )
            l2_clusters = _embedding_cluster(
                node_ids, l2_entity_texts, distance_threshold=0.25
            )
            logger.info(
                f"[Community] L2 embedding clusters: {len(l2_clusters)} clusters | "
                f"sizes: min={min(len(c) for c in l2_clusters)}, "
                f"max={max(len(c) for c in l2_clusters)}, "
                f"avg={sum(len(c) for c in l2_clusters) / len(l2_clusters):.1f}"
            )

            entity_to_l2: dict[str, str] = {}  # entity_node_id → L2 community_id
            l2_community_ids: list[str] = []
            for cluster_index, member_node_ids in enumerate(l2_clusters):
                if _tracker.cancel_recompute.is_set():
                    logger.info(
                        f"[Community] Recompute cancelled at L2 cluster "
                        f"{cluster_index + 1}/{len(l2_clusters)} — "
                        "ingestion/newer run arrived; will restart after next idle window."
                    )
                    return created
                logger.info(
                    f"[Community] Summarising L2 cluster {cluster_index + 1}/{len(l2_clusters)} "
                    f"({len(member_node_ids)} members)"
                )
                cid = _commit_community(
                    2,
                    member_node_ids,
                    [],
                    f"L2 cluster {cluster_index + 1}/{len(l2_clusters)}",
                )
                if cid:
                    l2_community_ids.append(cid)
                    for nid in member_node_ids:
                        entity_to_l2[nid] = cid

            l2_orphan_ids = [nid for nid in node_ids if nid not in entity_to_l2]
            logger.info(
                f"[Community] L2 complete: {len(l2_community_ids)} communities, "
                f"{len(l2_orphan_ids)} orphan entity nodes"
            )

            # ─── Level 1: embedding cluster on L2 communities only ─────────────
            # Orphan entities (not in any L2 community) are excluded — they weren't
            # clusterable at L2 and would only bloat higher levels.
            l1_super_ids = l2_community_ids
            l1_super_texts: list[str] = [
                (
                    f"{community_names.get(_sid, _sid)}: {community_summaries[_sid]}"
                    if _sid in community_summaries
                    else _sid
                )
                for _sid in l1_super_ids
            ]
            logger.info(
                f"[Community] Embedding {len(l1_super_ids)} L2 communities for L1 similarity clustering"
            )
            l1_clusters = _embedding_cluster(l1_super_ids, l1_super_texts)
            logger.info(
                f"[Community] L1 embedding clusters: {len(l1_clusters)} clusters"
            )

            entity_to_l1: dict[str, str] = {}
            l1_community_ids: list[str] = []
            for cluster_index, super_cluster in enumerate(l1_clusters):
                if _tracker.cancel_recompute.is_set():
                    logger.info(
                        f"[Community] Recompute cancelled at L1 cluster "
                        f"{cluster_index + 1}/{len(l1_clusters)} — "
                        "ingestion/newer run arrived; will restart after next idle window."
                    )
                    return created

                # Resolve super-nodes → entity IDs and build rollup inputs.
                l1_member_entity_ids: list[str] = []
                l1_rollup_rows: list[dict] = []
                for super_id in super_cluster:
                    if super_id in community_entity_members:
                        # L2 community: expand to its entity members.
                        l1_member_entity_ids.extend(community_entity_members[super_id])
                        if super_id in community_summaries:
                            l1_rollup_rows.append(
                                {
                                    "name": community_names[super_id],
                                    "summary": community_summaries[super_id],
                                }
                            )

                logger.info(
                    f"[Community] Summarising L1 cluster {cluster_index + 1}/{len(l1_clusters)} "
                    f"({len(l1_member_entity_ids)} entity members from {len(super_cluster)} super-nodes)"
                )
                cid = _commit_community(
                    1,
                    l1_member_entity_ids,
                    l1_rollup_rows,
                    f"L1 cluster {cluster_index + 1}/{len(l1_clusters)}",
                )
                if cid:
                    l1_community_ids.append(cid)
                    for nid in l1_member_entity_ids:
                        entity_to_l1[nid] = cid

            logger.info(f"[Community] L1 complete: {len(l1_community_ids)} communities")

            # ─── Level 0: embedding cluster on L1 communities only ─────────────────
            # Only L1 communities feed L0 — unabsorbed L2s and orphan entities are
            # excluded to keep the hierarchy a true compression at each level.
            l0_super_ids = l1_community_ids
            l0_super_texts: list[str] = [
                (
                    f"{community_names.get(_sid, _sid)}: {community_summaries[_sid]}"
                    if _sid in community_summaries
                    else _sid
                )
                for _sid in l0_super_ids
            ]
            logger.info(
                f"[Community] Embedding {len(l0_super_ids)} L1 communities for L0 similarity clustering"
            )
            l0_clusters = _embedding_cluster(
                l0_super_ids, l0_super_texts, distance_threshold=0.75
            )
            logger.info(
                f"[Community] L0 embedding clusters: {len(l0_clusters)} clusters"
            )

            for cluster_index, super_cluster in enumerate(l0_clusters):
                if _tracker.cancel_recompute.is_set():
                    logger.info(
                        f"[Community] Recompute cancelled at L0 cluster "
                        f"{cluster_index + 1}/{len(l0_clusters)} — "
                        "ingestion/newer run arrived; will restart after next idle window."
                    )
                    return created

                l0_member_entity_ids: list[str] = []
                l0_rollup_rows: list[dict] = []
                for super_id in super_cluster:
                    if super_id in community_entity_members:
                        # L1 community: expand to its entity members.
                        l0_member_entity_ids.extend(community_entity_members[super_id])
                        if super_id in community_summaries:
                            l0_rollup_rows.append(
                                {
                                    "name": community_names[super_id],
                                    "summary": community_summaries[super_id],
                                }
                            )
                    elif super_id in node_lookup:
                        # Orphan entity node: contribute directly.
                        l0_member_entity_ids.append(super_id)
                        _n = node_lookup[super_id]
                        l0_rollup_rows.append(
                            {
                                "name": _n.get("name") or super_id,
                                "summary": (
                                    f"A {_n.get('type', 'node')} named {_n.get('name') or super_id}."
                                ),
                            }
                        )

                logger.info(
                    f"[Community] Summarising L0 cluster {cluster_index + 1}/{len(l0_clusters)} "
                    f"({len(l0_member_entity_ids)} entity members from {len(super_cluster)} super-nodes)"
                )
                _commit_community(
                    0,
                    l0_member_entity_ids,
                    l0_rollup_rows,
                    f"L0 cluster {cluster_index + 1}/{len(l0_clusters)}",
                )

            # Refresh ES relationship_natural_language for nodes whose membership changed.
            # Community fields are no longer stored on regular nodes — only the NL sentence
            # written above carries that signal for retrieval. Collected into a
            # single batched Meili write (one task wait instead of one per node).
            # Names and relationship NL are batch-fetched from Qdrant in two calls
            # instead of three round-trips per node.
            _refresh_ids = list(level_assignments.keys())
            _refresh_content = (
                self._qdrant.get_nodes_content_by_ids(_refresh_ids)
                if _refresh_ids
                else {}
            )
            _refresh_rels = (
                self._qdrant.get_relationships_for_node_ids(_refresh_ids)
                if _refresh_ids
                else []
            )
            _refresh_id_set = set(_refresh_ids)
            _rel_nl_by_node: dict[str, list[str]] = {}
            for _rel_row in _refresh_rels:
                _nl = _rel_row.get("natural_language")
                if not _nl:
                    continue
                for _endpoint_key in ("source_node_id", "target_node_id"):
                    _endpoint = _rel_row.get(_endpoint_key)
                    if _endpoint in _refresh_id_set:
                        _bucket = _rel_nl_by_node.setdefault(_endpoint, [])
                        if _nl not in _bucket:
                            _bucket.append(_nl)

            community_rows: list[dict] = []
            for node_id in level_assignments:
                community_rows.append(
                    {
                        "node_id": node_id,
                        "relationship_natural_language": " ".join(
                            _rel_nl_by_node.get(node_id, [])
                        ),
                        "name": (
                            _refresh_content.get(node_id, {}).get("name") or ""
                        ),
                    }
                )
            self._meili.update_nodes_community(community_rows)

            # ── Compute & store 3D positions for the new layout ──────────────────
            try:
                from app.utils.graph_layout import compute_spring_layout_3d

                # After Leiden has created communities, rerun spring layout so
                # community nodes are pulled into the correct positions by their members.
                spring_node_ids, spring_edges = self._graph.get_all_node_ids_and_edges()
                positions = compute_spring_layout_3d(spring_node_ids, spring_edges)
                self._graph.store_node_positions(positions)
                logger.info(
                    f"[Community] Spring layout recomputed after Leiden: "
                    f"{len(positions)} nodes, {len(spring_edges)} edges"
                )
            except Exception as _layout_err:  # pylint: disable=broad-exception-caught
                logger.warning(
                    f"[Community] 3D layout computation failed (non-fatal): {_layout_err}"
                )

            logger.info(
                f"\n{'='*70}\n"
                f"[Community] Embedding recompute COMPLETE: {created} community nodes rebuilt\n"
                f"  Entities: {len(node_ids)}\n"
                f"  Levels: L2 → L1 → L0 (embedding similarity hierarchy)\n"
                f"{'='*70}"
            )
            return created
        finally:
            # Always release single-flight ownership so a newer queued request can proceed.
            with self._community_run_state_lock:
                if self._community_run_active_seq == requested_seq:
                    self._community_run_running = False
                    self._community_run_active_seq = 0

    def build_temporal_digests(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, period: str | None = None
    ) -> int:
        """Build temporal digest nodes that summarise all notes grouped by time period.

        Scrolls every ``isolated_context`` Qdrant point that carries a
        ``note_created_at`` payload field, buckets the contexts by the requested
        *period* granularity ("month" | "week" | "year"), generates an LLM summary
        for each non-empty bucket, then stores the result as a ``kind='temporal_digest'``
        node in Kuzu, Qdrant (node_cores), and Meilisearch.

        Existing digest nodes are cleared before each run so the set stays current.
        Returns the number of digest nodes created.
        """
        from datetime import datetime

        from app.core.config import settings as _settings

        if not _settings.TEMPORAL_DIGESTS_ENABLED:
            logger.info(
                "[TemporalDigest] Feature disabled via TEMPORAL_DIGESTS_ENABLED — skipping."
            )
            return 0

        # Guard: refuse to start while ingestion is active.
        if _tracker.has_active_ingestions(self.kb_id):
            logger.info(
                "[TemporalDigest] Skipping — ingestion is active; "
                "timer will restart when ingestion completes."
            )
            return 0

        _tracker.cancel_temporal.clear()
        self._temporal_digest_running = True

        _period = period or _settings.TEMPORAL_DIGEST_PERIOD
        logger.info(
            f"\n{'='*70}\n"
            f"[TemporalDigest] Starting build  period={_period}\n"
            f"{'='*70}"
        )

        # ── 1. Fetch all isolated_context points that carry a date ────────────
        all_points = self._qdrant.scroll_all_isolated_contexts_with_dates()
        if not all_points:
            logger.warning(
                "[TemporalDigest] No dated contexts found — ingest some notes first."
            )
            self._temporal_digest_running = False
            return 0
        logger.info(f"[TemporalDigest] Found {len(all_points)} dated context chunk(s).")

        # ── 2. Bucket by time period ──────────────────────────────────────────
        def _bucket(date_str: str) -> str | None:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if _period == "year":
                    return dt.strftime("%Y")
                if _period == "week":
                    return dt.strftime("%G-W%V")  # ISO week (e.g. "2024-W21")
                return dt.strftime("%Y-%m")  # month (default)
            except (ValueError, TypeError, AttributeError):
                return None

        buckets: dict[str, list[str]] = defaultdict(list)
        for payload in all_points:
            key = _bucket(payload.get("note_created_at", ""))
            if key:
                content = (payload.get("content") or "").strip()
                if content:
                    buckets[key].append(content)

        if not buckets:
            logger.warning(
                "[TemporalDigest] No valid period buckets — nothing to build."
            )
            self._temporal_digest_running = False
            return 0

        logger.info(
            f"[TemporalDigest] {len(buckets)} period bucket(s): {sorted(buckets.keys())}"
        )

        # ── 3. Clear existing temporal digest nodes ───────────────────────────
        old_ids = self._graph.clear_all_temporal_digests()
        logger.info(f"[TemporalDigest] Cleared {len(old_ids)} old digest node(s).")
        for old_id in old_ids:
            self._qdrant.delete_node(old_id)
            self._meili.delete_node(old_id)

        # ── 4. Build one digest node per bucket ───────────────────────────────
        built = 0
        for period_key in sorted(buckets.keys()):
            # Cooperative cancellation: stop between buckets if ingestion arrived.
            if _tracker.cancel_temporal.is_set():
                logger.info(
                    f"[TemporalDigest] Cancelled by ingestion after {built} bucket(s) — "
                    "rescheduling."
                )
                self._temporal_digest_running = False
                if not _tracker.has_active_ingestions(self.kb_id):
                    with self._temporal_digest_timer_lock:
                        if self._temporal_digest_timer is not None:
                            self._temporal_digest_timer.cancel()
                        self._temporal_digest_timer = threading.Timer(
                            COMMUNITY_IDLE_SECONDS,
                            self.build_temporal_digests,
                        )
                        self._temporal_digest_timer.daemon = True
                        self._temporal_digest_timer.start()
                    logger.info(
                        f"[TemporalDigest] Rescheduled in {COMMUNITY_IDLE_SECONDS}s."
                    )
                else:
                    logger.info(
                        "[TemporalDigest] Ingestion still active — "
                        "timer will restart when last ingestion ends."
                    )
                return built

            contexts = buckets[period_key]

            # Human-readable label
            try:
                if _period == "year":
                    label = period_key  # "2024"
                elif _period == "week":
                    year_str, week_str = period_key.split("-W")
                    label = f"Week {week_str}, {year_str}"
                else:
                    dt = datetime.strptime(period_key, "%Y-%m")
                    label = dt.strftime("%B %Y")  # "May 2024"
            except (ValueError, AttributeError):
                label = period_key

            # Truncate combined contexts to avoid LLM context overflow
            combined = "\n---\n".join(contexts)
            if len(combined) > 12_000:
                combined = combined[:12_000] + "\n...[truncated]"

            logger.info(
                f"[TemporalDigest] Summarising {len(contexts)} chunk(s) for {period_key}…"
            )
            try:
                summary = self._llm.generate_text(
                    system_prompt=(
                        "You are a knowledge synthesis assistant. "
                        "Summarize the main topics, events, and themes from the provided "
                        "documents into a concise paragraph. They may be personal notes, "
                        "course material, meeting records, or reference material — "
                        "describe what they contain, without assuming who wrote them "
                        "or why. "
                        "Return only the summary paragraph — no headers, no bullet points."
                    ),
                    user_prompt=(
                        f"The following contexts are from notes created during {label}:\n\n"
                        f"{combined}"
                    ),
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    f"[TemporalDigest] LLM summary failed for {period_key}: {exc}"
                )
                summary = f"Notes from {label}."

            node_id = f"digest_{_period}_" + period_key.replace("-", "_").replace(
                "W", "w"
            )
            node_name = f"{label} — {_period.capitalize()} Digest"

            # Store in graph + Qdrant
            self._graph.create_temporal_digest_node(
                node_id=node_id,
                name=node_name,
                summary=summary,
                period_key=period_key,
            )
            # Index in Meilisearch for full-text search
            self._meili.index_node(
                node_id=node_id,
                name=node_name,
                node_type="temporal_digest",
                isolated_contexts_text=summary,
            )
            logger.info(f"[TemporalDigest] Built '{node_name}'")
            built += 1

        logger.info(f"[TemporalDigest] Done — {built} digest node(s) created.")
        self._temporal_digest_running = False
        return built

    def get_maintenance_status(self) -> dict:
        """Return the running state of background maintenance jobs for this KB."""
        tracker = _tracker.get_status_snapshot(self.kb_id)
        return {
            "community_detection": {
                "running": self._community_run_running
                or bool(tracker.get("community_recompute_running")),
                "pending_nodes": tracker.get("pending_community_nodes", 0),
                "needed": bool(tracker.get("community_recompute_needed")),
                "timer_armed": bool(tracker.get("community_timer_armed")),
                "idle_seconds": tracker.get("community_idle_seconds"),
            },
            "temporal_digests": {"running": self._temporal_digest_running},
            "ingestion": {
                "active": int(tracker.get("active_ingestions") or 0),
                "last_completed_at": tracker.get("last_ingestion_at"),
            },
            "healthy": True,
        }


ingestion_workflow = IngestionWorkflow()
