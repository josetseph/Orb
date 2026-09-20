"""Hybrid retrieval: vector search, graph traversal, keyword search, and cross-encoder reranking."""

# pylint: disable=too-many-lines,import-outside-toplevel
import asyncio
import calendar
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import List

from app.core.config import settings
from app.core.log import get_logger
from app.services.graph import GraphService, graph_service
from app.services.qdrant_service import QdrantService, qdrant_service
from app.services.meilisearch_service import MeilisearchService, meilisearch_service
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

# Suppress noisy tokenizer warnings
logging.getLogger("transformers.tokenization_utils_base").setLevel(logging.ERROR)

logger = get_logger("RetrievalService")


class RetrievalService:
    """Orchestrates multi-stage retrieval: vector → keyword → graph expansion → reranking."""

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
        # Per-KB LLM override, or the global service (resolved lazily so the
        # module-level singleton is not constructed at import).
        self._llm_override = llm
        logger.info("RetrievalService initialized")

    @property
    def _llm(self):
        if self._llm_override is not None:
            return self._llm_override
        from app.services.llm import llm_service

        return llm_service

    def _log_retrieval_details(self, query: str, results: List[dict], query_entities: List[str]):
        """One DEBUG line per retrieval: what came back and the text handed to the LLM."""
        summary = {
            "query": query,
            "entities": query_entities,
            "results": [
                {
                    "name": doc.get("original_obj", {}).get("name"),
                    "type": doc.get("type"),
                    "score": doc.get("final_score"),
                    "boosts": doc.get("boosts"),
                    "linked_notes": [n.get("id") for n in doc.get("linked_notes", [])],
                    "text": doc.get("text", ""),
                }
                for doc in results
            ],
        }
        logger.debug(json.dumps(summary, default=str))

    def _get_node_relationships(  # pylint: disable=too-many-locals,too-many-branches
        self, node: dict, max_relationships: int = 5
    ) -> list[dict]:
        """
        Fetch 1-hop relationships for a node.
        Returns a list of dicts: {nl_sentence, neighbour_name, neighbour_type, neighbour_context}
        """

        try:
            node_name = node.get("name", "")
            if not node_name:
                return []

            related_nodes = self._graph.get_related_nodes(
                node_name=node_name,
                max_depth=1,
                node_id=node.get("node_id") or node.get("id") or None,
            )
            if not related_nodes:
                return []

            # Collect neighbour node_ids for bulk Qdrant enrichment
            neighbour_id_to_related = {}
            for related in related_nodes:
                nid = related.get("node_id") or related.get("id")
                if nid:
                    neighbour_id_to_related[nid] = related

            neighbour_content: dict[str, dict] = {}
            if neighbour_id_to_related:
                try:
                    neighbour_content = self._qdrant.get_nodes_content_by_ids(
                        list(neighbour_id_to_related.keys())
                    )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.debug(
                        f"  [Relationships] Qdrant neighbour enrichment failed: {e}"
                    )

            entries = []
            for related in related_nodes[:max_relationships]:
                neighbour_name = related.get("name", "")
                if not neighbour_name or neighbour_name == "None":
                    continue

                # Build NL sentence
                nl_path = related.get("natural_language_path", [])
                rel_path = related.get("relationship_path", [])
                ctx_path = related.get("context_path", [])

                if nl_path and nl_path[0] and "None" not in nl_path[0]:
                    nl_sentence = nl_path[0]
                elif ctx_path and ctx_path[0]:
                    nl_sentence = f"{node_name} {ctx_path[0]} {neighbour_name}."
                elif rel_path:
                    nl_sentence = f"{node_name} {rel_path[0].replace('_', ' ').lower()} {neighbour_name}."
                else:
                    nl_sentence = f"{node_name} is connected to {neighbour_name}."

                # Get neighbour type and context
                nid = related.get("node_id") or related.get("id")
                neighbour_type = related.get("entity_type", "") or ""
                neighbour_context = ""
                if nid and nid in neighbour_content:
                    c = neighbour_content[nid]
                    # isolated_contexts is the primary content source
                    ctxs = c.get("isolated_contexts") or []
                    neighbour_context = ctxs[0] if ctxs else ""
                    if not neighbour_type:
                        neighbour_type = c.get("type", "") or ""

                entries.append(
                    {
                        "nl_sentence": nl_sentence,
                        "neighbour_name": neighbour_name,
                        "neighbour_type": neighbour_type,
                        "neighbour_context": neighbour_context,
                    }
                )

            return entries

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug(
                f"  [Relationships] Failed for {node.get('name', 'unknown')}: {e}"
            )
            return []

    async def _expand_relevant_neighbors(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self,
        relevant_docs: list[dict],
        question: str,
        surfaced_names: set[str],
    ) -> list[dict]:
        """
        Expand the retrieval frontier from already-relevant nodes.

        For each relevant node, fetch its 1-hop neighbours not yet surfaced.
        Ask the LLM (in natural language format) to select which of those
        relationships provide additional evidence for the question.
        Returns the selected neighbour nodes as standard doc dicts.
        """

        relationship_entries: list[dict] = []

        for doc in relevant_docs:
            node = doc.get("original_obj") or {}
            node_name = node.get("name", "")
            if not node_name:
                continue
            # Prefer node_id stored in original_obj; fall back to "id" key
            src_node_id = node.get("node_id") or node.get("id") or ""
            # All nodes are :Indexable — no label filter needed
            try:
                related = self._graph.get_related_nodes(
                    node_name=node_name,
                    max_depth=1,
                    node_id=src_node_id or None,
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.debug(
                    f"  [GraphExpand] get_related_nodes failed for {node_name}: {e}"
                )
                continue

            for r in related:
                neighbor_name = r.get("name", "")
                if not neighbor_name or neighbor_name in surfaced_names:
                    continue
                nl_path = r.get("natural_language_path") or []
                nl_sentence = nl_path[0] if nl_path and nl_path[0] else None
                rel_path = r.get("relationship_path") or []
                rel_type = rel_path[0] if rel_path else "connected_to"
                ctx_path = r.get("context_path") or []
                context = ctx_path[0] if ctx_path else None
                edge_direction = r.get("edge_direction", "outgoing")
                relationship_entries.append(
                    {
                        "source": node_name,
                        "src_node_id": src_node_id,
                        "rel_type": rel_type,
                        "nl_sentence": nl_sentence,
                        "neighbor": r,
                        "context": context,
                        "edge_direction": edge_direction,
                    }
                )

        if not relationship_entries:
            logger.info("  [GraphExpand] No new 1-hop neighbors to evaluate.")
            return []

        # --- Enrich nl_sentence from Qdrant node_relationships ---
        # Kuzu always returns NULL for natural_language_path; the real NL text
        # lives in the Qdrant node_relationships collection, indexed by
        # (source_node_id, target_node_id).  Build a lookup from both directions
        # so we can fill nl_sentence regardless of which end was the anchor.
        try:
            _all_node_ids = list(
                {
                    nid
                    for e in relationship_entries
                    for nid in (e.get("src_node_id"), e["neighbor"].get("node_id"))
                    if nid
                }
            )
            if _all_node_ids:
                _qdrant_rels = self._qdrant.get_relationships_for_node_ids(
                    _all_node_ids
                )
                # Build bidirectional lookup: (src_id, tgt_id) → nl_text
                _nl_lookup: dict[tuple[str, str], str] = {}
                for _qr in _qdrant_rels:
                    _s = _qr.get("source_node_id", "")
                    _t = _qr.get("target_node_id", "")
                    _nl = _qr.get("natural_language", "")
                    if _s and _t and _nl:
                        _nl_lookup[(_s, _t)] = _nl

                for entry in relationship_entries:
                    if entry.get("nl_sentence"):
                        entry.setdefault("_nl_is_reverse", False)
                        continue  # already set (shouldn't happen with current Kuzu)
                    _src = entry.get("src_node_id", "")
                    _tgt = entry["neighbor"].get("node_id", "")
                    _src_name = entry.get("source", "")
                    _tgt_name = entry["neighbor"].get("name", "")
                    nl = _nl_lookup.get((_src, _tgt))
                    if nl:
                        entry["nl_sentence"] = nl
                        entry["_nl_is_reverse"] = False
                    else:
                        nl = _nl_lookup.get((_tgt, _src))
                        if nl:
                            # Predicate was written from the neighbor's perspective.
                            entry["nl_sentence"] = nl
                            entry["_nl_is_reverse"] = True
        except Exception as _qdrant_err:  # pylint: disable=broad-exception-caught
            logger.debug(f"  [GraphExpand] Qdrant NL lookup failed: {_qdrant_err}")

        logger.info(
            f"  [GraphExpand] Evaluating {len(relationship_entries)} 1-hop relationship(s):"
        )
        for _i, _e in enumerate(relationship_entries):
            if _e.get("nl_sentence"):
                _nl = _e["nl_sentence"]
            elif _e.get("edge_direction") == "incoming":
                _nl = f'{_e["neighbor"].get("name", "?")} --[{_e["rel_type"]}]--> {_e["source"]}'
            else:
                _nl = f'{_e["source"]} --[{_e["rel_type"]}]--> {_e["neighbor"].get("name", "?")}'
            logger.info(f"    [{_i}] {_nl}")

        # Enrich neighbor nodes with content from Qdrant before LLM filtering
        _neighbor_ids = [
            e["neighbor"].get("node_id")
            for e in relationship_entries
            if e["neighbor"].get("node_id")
        ]
        if _neighbor_ids:
            try:
                _qdrant_neighbor_content = self._qdrant.get_nodes_content_by_ids(
                    _neighbor_ids
                )
                for entry in relationship_entries:
                    _nid = entry["neighbor"].get("node_id")
                    if _nid and _nid in _qdrant_neighbor_content:
                        _c = _qdrant_neighbor_content[_nid]
                        entry["neighbor"]["description"] = _c.get("description", "")
                        entry["neighbor"]["summary"] = _c.get("description", "")
                        entry["neighbor"]["isolated_contexts"] = _c.get(
                            "isolated_contexts", []
                        )
                        # Propagate entity type from Qdrant when Kuzu didn't supply one
                        if not entry["neighbor"].get("entity_type"):
                            entry["neighbor"]["entity_type"] = _c.get("type", "")
            except Exception as _e:  # pylint: disable=broad-exception-caught
                logger.debug(f"  [GraphExpand] Qdrant neighbor enrichment failed: {_e}")

        if not relationship_entries:
            return []

        # Build origin node lookup (needed for per-neighbor reranker text below)
        origin_obj_by_name: dict[str, dict] = {}
        origin_text_by_name: dict[str, str] = {}
        for doc in relevant_docs:
            _node = doc.get("original_obj", {})
            _name = _node.get("name", "")
            _text = (
                doc.get("text")
                or _node.get("summary")
                or _node.get("description")
                or ""
            ).strip()
            if _name:
                origin_obj_by_name[_name] = _node
                if _text:
                    origin_text_by_name[_name] = _text

        # Rank each (root, neighbor) pair individually with the reranker, then
        # keep only the top N — so the merged root doc only contains the neighbors
        # the reranker considers relevant to this question.
        if len(relationship_entries) > settings.GRAPH_EXPAND_TOP_NEIGHBORS:
            from app.services.reranker import reranker_service

            _per_neighbor_texts: list[str] = []
            for entry in relationship_entries:
                _origin_node = origin_obj_by_name.get(
                    entry["source"], {"name": entry["source"]}
                )
                neighbor = entry["neighbor"]
                nl = entry.get("nl_sentence")
                if nl:
                    _is_incoming = entry.get("_nl_is_reverse", False)
                else:
                    _is_incoming = entry.get("edge_direction") == "incoming"
                    nl = (entry.get("rel_type") or "connected_to").replace("_", " ")
                _rel_entry = {
                    "nl_sentence": nl,
                    "neighbour_name": neighbor.get("name", ""),
                    "neighbour_type": neighbor.get("entity_type") or "",
                    "neighbour_context": self._get_node_text(neighbor)
                    or neighbor.get("name", ""),
                    "is_incoming": _is_incoming,
                }
                _per_neighbor_texts.append(
                    self._build_node_text(_origin_node, [_rel_entry])
                )

            _scores = await reranker_service.rerank(question, _per_neighbor_texts)
            _score_map: dict[int, float] = (
                {
                    r["index"]: float(r.get("relevance_score") or 0.0)
                    for r in _scores
                    if "index" in r
                }
                if _scores
                else {}
            )
            for i, entry in enumerate(relationship_entries):
                entry["_expand_score"] = _score_map.get(i, 0.0)
                logger.debug(
                    f"  [GraphExpand] {entry['source']} → {entry['neighbor'].get('name','?')} "
                    f"score={entry['_expand_score']:.4f}"
                )
            relationship_entries.sort(
                key=lambda e: e.get("_expand_score", 0.0), reverse=True
            )
            _before = len(relationship_entries)
            relationship_entries = relationship_entries[
                : settings.GRAPH_EXPAND_TOP_NEIGHBORS
            ]
            _thresh = settings.GRAPH_EXPAND_SCORE_THRESHOLD
            if _thresh > 0:
                _before_thresh = len(relationship_entries)
                relationship_entries = [
                    e
                    for e in relationship_entries
                    if e.get("_expand_score", 0.0) >= _thresh
                ]
                if len(relationship_entries) < _before_thresh:
                    logger.info(
                        f"  [GraphExpand] Score threshold {_thresh} dropped "
                        f"{_before_thresh - len(relationship_entries)} neighbour(s)"
                        f" → {len(relationship_entries)} remaining"
                    )
            _top_scores = [f"{e['_expand_score']:.4f}" for e in relationship_entries]
            logger.info(
                f"  [GraphExpand] Ranked {_before} neighbours → kept top {len(relationship_entries)} "
                f"(scores: {_top_scores})"
            )
        else:
            logger.info(
                f"  [GraphExpand] Expanding all {len(relationship_entries)} neighbour(s) (at or below top-N limit)"
            )

        # Group entries by origin node — one merged doc per source so the root
        # node text appears exactly once, with all its relationships listed below.
        from collections import defaultdict

        entries_by_source: dict[str, list] = defaultdict(list)
        for entry in relationship_entries:
            if entry["neighbor"].get("name"):
                entries_by_source[entry["source"]].append(entry)

        selected_neighbor_names: list[str] = []
        result: list[dict] = []
        seen_neighbors: set[str] = set()

        for source_name, source_entries in entries_by_source.items():
            _origin_node = origin_obj_by_name.get(source_name, {})
            _origin_text = origin_text_by_name.get(source_name, "")
            rel_entries: list[dict] = []
            source_neighbor_names: list[str] = []

            for entry in source_entries:
                neighbor = entry["neighbor"]
                neighbor_name = neighbor.get("name", "")
                if not neighbor_name or neighbor_name in seen_neighbors:
                    continue
                seen_neighbors.add(neighbor_name)
                selected_neighbor_names.append(neighbor_name)
                source_neighbor_names.append(neighbor_name)

                node_text = self._get_node_text(neighbor)
                _neighbour_text = node_text if node_text else neighbor_name
                _n_type = neighbor.get("entity_type") or ""

                nl = entry.get("nl_sentence")
                # Determine sentence direction.
                # Forward Qdrant NL (_nl_is_reverse=False): "{source} {nl} {neighbor}"
                # Reverse Qdrant NL (_nl_is_reverse=True):  "{neighbor} {nl} {source}"
                #   (predicate was written from the neighbor's perspective)
                # No Qdrant NL: fall back to Kuzu edge_direction.
                if nl:
                    _is_incoming = entry.get("_nl_is_reverse", False)
                else:
                    _is_incoming = entry.get("edge_direction") == "incoming"
                    nl = (entry.get("rel_type") or "connected_to").replace("_", " ")

                rel_entries.append(
                    {
                        "nl_sentence": nl,
                        "neighbour_name": neighbor_name,
                        "neighbour_type": _n_type,
                        "neighbour_context": _neighbour_text,
                        "is_incoming": _is_incoming,
                    }
                )

            if not rel_entries:
                continue

            if _origin_node:
                _formatted_text = self._build_node_text(_origin_node, rel_entries)
            else:
                _lines = [_origin_text] if _origin_text else []
                for _n in rel_entries:
                    if _n["is_incoming"]:
                        _lines.append(
                            f"{_n['neighbour_name']} {_n['nl_sentence']} {source_name}."
                        )
                    else:
                        _lines.append(
                            f"{source_name} {_n['nl_sentence']} {_n['neighbour_name']}."
                        )
                    if _n["neighbour_context"]:
                        _lines.append(_n["neighbour_context"])
                _formatted_text = "\n".join(_lines)

            result.append(
                {
                    "text": _formatted_text,
                    "_rerank_text": _formatted_text,
                    "type": "graph_expansion",
                    "_origin_name": source_name,
                    "original_obj": _origin_node or {"name": source_name},
                    "linked_notes": [],
                    "_neighbor_names": source_neighbor_names,
                }
            )

        # Preserve note provenance: fetch notes for all selected neighbors and
        # attach them to the relevant merged doc.
        if result and selected_neighbor_names:
            try:
                evidence_rows = self._graph.get_linked_evidence(
                    selected_neighbor_names,
                    limit_per_node=2,
                )
                node_to_notes: dict[str, list[dict]] = {}
                for row in evidence_rows:
                    node_name = (row.get("node_name") or "").lower()
                    evidence = row.get("evidence", [])
                    if node_name and evidence:
                        node_to_notes[node_name] = evidence

                for doc in result:
                    doc_notes: list[dict] = []
                    for n_name in doc.get("_neighbor_names") or []:
                        doc_notes.extend(node_to_notes.get(n_name.lower(), []))
                    if doc_notes:
                        doc["linked_notes"] = doc_notes
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.debug(f"  [GraphExpand] get_linked_evidence failed: {e}")
            finally:
                for doc in result:
                    doc.pop("_neighbor_names", None)
        else:
            for doc in result:
                doc.pop("_neighbor_names", None)

        if result:
            logger.info(
                f"  [GraphExpand] Added {len(result)} neighbour node(s) via relationship expansion"
            )
        return result

    def _build_node_text(  # pylint: disable=too-many-branches
        self, node: dict, relationships: list[dict], brief_root: bool = False
    ) -> str:
        """
        Format a node and its relationships into a structured readable block.

        Format (brief_root=False, default):
            {name} is a {type}. {context}
            {name} {nl_sentence} {neighbour_name}.
            {neighbour_name} is a {neighbour_type}. {neighbour_context}

        Format (brief_root=True — used in expansion docs where the origin's full
        context already appears as a preceding entity_match/vector_match candidate):
            {name} is a {type}.      ← header only, no repeated context blob
            {name} {nl_sentence} {neighbour_name}.
            ...
        """
        name = node.get("name", "")
        entity_type = node.get("entity_type", "") or ""

        if brief_root:
            root_line = f"{name} is a {entity_type}." if entity_type else name
        else:
            summary = self._get_node_text(node)
            # Always lead with the type declaration when present so the LLM sees
            # "{name} is a {type}. {context}" rather than just raw context.
            # Only skip the bare "{name}." prefix (no type) when the summary
            # already opens with the name — the name is already present there.
            if entity_type:
                root_line = (
                    f"{name} is a {entity_type}. {summary}".strip()
                    if summary
                    else f"{name} is a {entity_type}."
                )
            elif summary and summary.lower().startswith(name.lower()):
                root_line = summary
            else:
                root_line = (f"{name}. {summary}" if summary else name).strip()
        lines = [root_line]

        for rel in relationships:
            nl = (rel.get("nl_sentence", "") or "").rstrip(". ")
            n_name = rel.get("neighbour_name", "")
            n_type = rel.get("neighbour_type", "") or ""
            n_ctx = rel.get("neighbour_context", "") or ""

            if not nl or not n_name:
                continue

            if rel.get("is_incoming"):
                lines.append(f"{n_name} {nl} {name}.")
            else:
                lines.append(f"{name} {nl} {n_name}.")

            # Build neighbour descriptor line.
            # If we have a type, prepend "{n_name} is a {n_type}." to the context.
            # If context already opens with the neighbour's name, skip a bare name prefix.
            if n_type:
                neighbour_line = f"{n_name} is a {n_type}."
                if n_ctx:
                    neighbour_line = f"{neighbour_line} {n_ctx}"
            elif n_ctx:
                neighbour_line = (
                    n_ctx
                    if n_ctx.lower().startswith(n_name.lower())
                    else f"{n_name}. {n_ctx}"
                )
            else:
                neighbour_line = None

            if neighbour_line:
                lines.append(neighbour_line)

        return "\n".join(lines)

    def _get_node_text(self, node: dict) -> str:
        """
        Extract text content from a node, including atomic facts so the LLM
        can answer fact-specific questions (e.g. "Where is X from?") directly
        from candidate text without needing a separate lookup.

        Args:
            node: Node dict from Kuzu
        """
        _isolated_contexts = node.get("isolated_contexts") or []
        _isolated_joined = " ".join(s for s in _isolated_contexts if s)
        return node.get("summary") or node.get("description") or _isolated_joined

    async def _search_qdrant_multi_collection(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self,
        query_vector: list[float],
        date_filter: str | None = None,
        period_filter: str | None = None,
    ) -> list[dict]:
        """Search all Qdrant collections and normalize into node-like results.

        Per plan: no result limiting — returns ALL hits above the similarity
        threshold.  A large ceiling (500 per collection) is passed to the
        Qdrant client solely because its API requires a numeric limit; the
        score_threshold is the only real filter.

        Each collection hit carries a ``parent_node_id`` and optionally a
        ``name`` field added at upsert time.  When ``name`` is present it is
        used directly; otherwise the hit is skipped so the caller's dedup
        logic stays consistent.
        """
        # The reranker re-scores all candidates — use the lower pre-rerank
        # threshold so borderline-but-relevant nodes aren't discarded before
        # the reranker even sees them.
        _threshold = settings.VECTOR_PRE_RERANK_THRESHOLD
        _contexts_filter: Filter | None = None
        _period_key_filter: str | None = None
        _day_only: bool = False
        if date_filter:
            # Specific day — exact match on isolated_contexts; exclude month-level
            # summaries (communities, temporal_digests) from node_cores.
            _contexts_filter = Filter(
                must=[
                    FieldCondition(
                        key="note_created_at", match=MatchValue(value=date_filter)
                    )
                ]
            )
            _day_only = True
            logger.info(
                f"  [Qdrant] Applying day filter: note_created_at={date_filter}"
            )
        elif period_filter:
            # Month range — match all dates in the month; include communities and
            # temporal_digests with the matching period_key.
            try:
                year, month_num = int(period_filter[:4]), int(period_filter[5:7])
                _, last_day = calendar.monthrange(year, month_num)
                month_dates = [
                    f"{year}-{month_num:02d}-{d:02d}" for d in range(1, last_day + 1)
                ]
                _contexts_filter = Filter(
                    must=[
                        FieldCondition(
                            key="note_created_at", match=MatchAny(any=month_dates)
                        )
                    ]
                )
                _period_key_filter = period_filter
                logger.info(
                    f"  [Qdrant] Applying month filter: period={period_filter} ({len(month_dates)} dates)"
                )
            except (ValueError, IndexError) as exc:
                logger.warning(
                    f"  [Qdrant] Invalid period_filter '{period_filter}': {exc}"
                )
        hits = self._qdrant.search_all_collections(
            query_vector=query_vector,
            limit=500,  # large ceiling; score_threshold is the real filter
            min_score=_threshold,
            contexts_filter=_contexts_filter,
            period_key_filter=_period_key_filter,
            day_only=_day_only,
        )
        if not hits:
            logger.info("  [Qdrant] No hits above similarity threshold.")
            return []
        logger.info(
            f"  [Qdrant] Raw hits from search_all_collections: {len(hits)} "
            f"(threshold={_threshold})"
        )
        merged: dict[str, dict] = {}
        unresolved_node_ids: set[str] = set()
        normalized_hits: list[dict] = []
        for hit in hits:
            payload = hit.get("payload", {})
            score = float(hit.get("score", 0.0))
            collection = hit.get("collection", "")
            node_id = (
                payload.get("node_id")
                or payload.get("parent_node_id")
                or payload.get("source_node_id")
                or payload.get("target_node_id")
            )
            name = (payload.get("name") or "").strip()
            if not name and node_id:
                unresolved_node_ids.add(node_id)

            normalized_hits.append(
                {
                    "payload": payload,
                    "score": score,
                    "collection": collection,
                    "node_id": node_id,
                    "name": name,
                }
            )

        resolved_names: dict[str, str] = {}
        if unresolved_node_ids:
            try:
                core_rows = self._qdrant.get_nodes_content_by_ids(
                    list(unresolved_node_ids)
                )
                for _nid, _row in core_rows.items():
                    _name = (_row.get("name") or "").strip()
                    if _name:
                        resolved_names[_nid] = _name
            except Exception as _e:  # pylint: disable=broad-exception-caught
                logger.debug(f"  [Qdrant] Failed resolving missing hit names: {_e}")

        for entry in normalized_hits:
            payload = entry["payload"]
            score = entry["score"]
            collection = entry["collection"]
            node_id = entry["node_id"]
            name = entry["name"] or resolved_names.get(node_id or "", "")
            if not name:
                continue

            key = name.lower()
            is_relationship_hit = "relationships" in collection
            is_isolated_context_hit = "isolated_context" in collection

            if is_isolated_context_hit:
                # Always accumulate isolated context text — never discard it just
                # because another hit for the same node scored higher. Nodes with
                # an empty description in node_cores would otherwise get an empty
                # summary and be silently filtered out by _get_node_text.
                ctx_text = (payload.get("content") or "").strip()
                ctx_date = (payload.get("note_created_at") or "").strip()
                if ctx_date:
                    ctx_text = f"{ctx_text} - {ctx_date}"
                if key in merged:
                    existing_summary = merged[key].get("summary") or ""
                    if ctx_text and ctx_text not in existing_summary:
                        merged[key]["summary"] = (
                            existing_summary + " " + ctx_text
                        ).strip()
                    # Keep the best score seen so far
                    if score > merged[key].get("score", 0.0):
                        merged[key]["score"] = score
                else:
                    merged[key] = {
                        "id": node_id,
                        "node_id": node_id,
                        "name": name,
                        "entity_type": payload.get("type") or "",
                        "summary": ctx_text,
                        "score": score,
                        "_source": "qdrant",
                    }
                continue

            current = merged.get(key)
            new_summary = (
                payload.get("natural_language")
                if is_relationship_hit
                else (
                    payload.get("description")
                    or payload.get("content")
                    or payload.get("natural_language", "")
                )
            )
            if current:
                if current.get("score", 0.0) >= score:
                    # Keep existing entry but patch in missing fields
                    if not current.get("summary") and new_summary:
                        current["summary"] = new_summary
                    continue
                # Higher-scored non-isolated hit: keep its summary only if better
                if not new_summary and current.get("summary"):
                    new_summary = current["summary"]

            merged[key] = {
                "id": node_id,
                "node_id": node_id,
                "name": name,
                "entity_type": (
                    "" if is_relationship_hit else (payload.get("type") or "")
                ),
                "summary": new_summary,
                "score": score,
                "_source": "qdrant",
            }

        result = list(merged.values())

        # Back-fill entity_type from node_cores for any hits that still lack it.
        # isolated_context payloads only carry {parent_node_id, content}; node_cores
        # is the authoritative source of type and is cheap to batch-fetch by ID.
        missing = [n for n in result if not n.get("entity_type") and n.get("node_id")]
        if missing:
            try:
                cores = self._qdrant.get_nodes_content_by_ids(
                    [n["node_id"] for n in missing]
                )
                for n in missing:
                    n["entity_type"] = cores.get(n["node_id"], {}).get("type", "")
            except Exception as _e:  # pylint: disable=broad-exception-caught
                logger.debug(f"  [Qdrant] entity_type backfill failed: {_e}")

        logger.info(
            f"  [Qdrant] {len(result)} unique nodes after dedup (from {len(hits)} raw hits)"
        )
        return result

    async def _search_meili_by_keyword(self, query: str) -> list[dict]:
        """Search Meilisearch full-text index and normalize into node-like results."""
        hits = await asyncio.to_thread(self._meili.search_nodes, query, 100)
        if not hits:
            logger.info("  [Meili] No BM25 keyword hits.")
            return []
        logger.info(f"  [Meili] {len(hits)} raw Meili hits")

        merged: dict[str, dict] = {}
        for hit in hits:
            payload = hit.get("payload", {})
            score = float(hit.get("score", 0.0))
            name = (payload.get("name") or "").strip()
            if not name:
                continue

            key = name.lower()
            current = merged.get(key)
            if current and current.get("score", 0.0) >= score:
                continue

            merged[key] = {
                "id": payload.get("node_id"),
                "name": name,
                "entity_type": payload.get("type") or "",
                "summary": payload.get("description") or "",
                "score": score,
                "_source": "meili",
            }

        return list(merged.values())

    def _merge_search_results(
        self, primary: list[dict], secondary: list[dict], seen_names: set[str]
    ) -> list[dict]:
        """Merge and dedupe search results by node name while preserving primary precedence."""
        merged = list(primary)
        for node in secondary:
            name = node.get("name")
            if not name:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            merged.append(node)
        return merged

    async def hybrid_search(  # pylint: disable=too-many-nested-blocks,too-many-locals,too-many-branches,too-many-statements
        self, query: str, top_k: int = 50
    ) -> List[dict]:
        """
        Entity-First Retrieval Pipeline:

        Uses entity name matching as the primary entry point, falling back to
        vector/keyword search only when no usable entity content is found.
        1-hop graph expansion from matched nodes is used to surface related context.

        Flow:
        1. Query Analysis: Extract expected entity types and question attributes via LLM
        2. Entity Search: Find nodes by extracted entity names (primary entry point)
           2.5 Name Variant Expansion: catch alternate/fuller name forms for Person entities
        2. Vector+Keyword Fallback: only when entity matching yields no usable content
        3. Community + Note Grounding: community summaries for broad queries; linked-note traceability
        4. Candidate Preparation: build ranked candidate list (no artificial cutoff)

        Returns node summaries as primary evidence, not chunked note snippets.
        """
        from app.services.embedding import embedding_service

        logger.info(
            f"\n{'─'*60}\n"
            f"  [HybridSearch] query='{query}' top_k={top_k}\n"
            f"{'─'*60}"
        )
        t_start = time.perf_counter()
        t_phase_start = time.perf_counter()

        # Query Analysis with LLM structured outputs
        logger.info("  [HybridSearch] Calling LLM query analysis...")
        query_analysis = self._llm.analyze_query(query)

        # Extract expected entity types for filtering/boosting
        expected_entity_types = [
            t.lower() for t in query_analysis.get("expected_entity_types", [])
        ]
        question_attribute = query_analysis.get("question_attribute", None)
        query_keywords = query_analysis.get("keywords", [])

        # Build an enriched query string for vector embedding and reranking.
        # Appending the question attribute sharpens semantic focus — e.g.
        # "Were Scott Derrickson and Ed Wood of the same nationality?"
        # becomes "...nationality" so vectors cluster around nationality facts.
        enriched_query = f"{query} {question_attribute}" if question_attribute else query

        # ============ ENTITY EXTRACTION ============
        # The LLM is the sole source of entity names from the query.
        # No regex/TitleCase fallback — the LLM already handles multi-word names.
        query_entities = list(query_analysis.get("entities", []))

        logger.info(
            f"  [LLM Analysis] Intent: {query_analysis.get('intent')}, "
            f"Entities: {query_entities}, "
            f"Expected Types: {expected_entity_types}, "
            f"Attribute: {question_attribute}"
        )
        query_date_filter: str | None = query_analysis.get("date_filter") or None
        query_period_filter: str | None = query_analysis.get("period_filter") or None
        if query_date_filter:
            logger.info(f"  [LLM Analysis] Day filter detected: {query_date_filter}")
        if query_period_filter:
            logger.info(
                f"  [LLM Analysis] Month filter detected: {query_period_filter}"
            )

        # Generate query embedding for vector search
        t_embedding_start = time.perf_counter()
        full_vector = embedding_service.embed_query(enriched_query)
        t_embedding = time.perf_counter() - t_embedding_start
        logger.info(f"  [⏱️ Timing] Total query embedding: {t_embedding:.2f}s")

        # ============ HYBRID RETRIEVAL ============
        # Combines entity name matching (for explicit mentions) with vector search
        # (for semantic similarity). This ensures we find both "Albert Einstein"
        # AND "Marie Curie" in comparison questions.

        t_phase_start = time.perf_counter()
        entity_nodes = []  # Found by name matching
        vector_nodes = []  # Found by vector search
        node_names_found = set()  # Track names to avoid duplicates

        # STEPS 1, 1b, and 2 hit three independent stores (Kuzu, Meilisearch,
        # Qdrant) so they run CONCURRENTLY. Results are merged afterwards in the
        # original precedence order (entity → BM25 → vector), so dedup behaviour
        # is unchanged.

        def _entity_branch() -> list[dict]:  # pylint: disable=too-many-branches
            """STEP 1 + 1.5: entity name matching with Person name-variant expansion."""
            if not query_entities:
                return []
            # Normalize to lowercase so lookups match stored (lowercase) names
            _normalized_query_entities = [e.lower().strip() for e in query_entities]
            entity_found = self._graph.find_nodes_by_name(
                names=_normalized_query_entities, fuzzy=True
            )
            # Collect ALL matching nodes first — do NOT dedup by name yet.
            # Multiple graph nodes can share the same name but only one may have
            # Qdrant content. Deduping before enrichment would silently drop the
            # node that has content whenever an empty duplicate appears first.
            _entity_candidates = []
            for node in entity_found:
                node["_source"] = "entity_match"
                _entity_candidates.append(node)

            # STEP 1.5: NAME VARIANT EXPANSION
            # Search for variants of found names to catch fuller/alternate versions
            # e.g., "Robert Smith" → also find "Robert Smith Jr."
            # Only Person entities with multi-word names are expanded; all
            # qualifying names go through one batched Kuzu lookup.
            _seen_variant_keys: set = set(
                (n.get("name") or "").lower().strip() for n in _entity_candidates
            )
            _expandable_names: list[str] = list(
                dict.fromkeys(
                    node.get("name", "")
                    for node in entity_found
                    if node.get("name")
                    and len(node.get("name", "").split()) >= 2
                    and (node.get("entity_type") or "").lower() == "person"
                )
            )
            variant_nodes = []
            if _expandable_names:
                try:
                    _variant_map = self._graph.find_name_variants_batch(
                        _expandable_names
                    )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.debug(f"  [Variant] Batch lookup failed: {e}")
                    _variant_map = {}
                for node_name in _expandable_names:
                    for v in _variant_map.get(node_name.lower().strip(), []):
                        _v_key = (v["name"] or "").lower().strip()
                        if (
                            _v_key not in _seen_variant_keys
                            and _v_key != node_name.lower().strip()
                        ):
                            v["_source"] = "name_variant"
                            v["_variant_of"] = node_name
                            variant_nodes.append(v)
                            _seen_variant_keys.add(_v_key)

            if variant_nodes:
                logger.info(
                    f"  [Entity] Found {len(variant_nodes)} name variants: "
                    f"{[(v['name'], v.get('_variant_of')) for v in variant_nodes]}"
                )
                _entity_candidates.extend(variant_nodes)

            # Dedup by node_id before enriching — the fuzzy UNWIND/CONTAINS
            # query can return the same node multiple times when it matches
            # more than one LLM-extracted query term (e.g. "ed wood" and
            # "wood" both hit the same node with different matched_query
            # values, so RETURN DISTINCT doesn't collapse them).
            # node_id is the canonical key: one ID → one Qdrant record.
            _seen_node_ids: dict[str, dict] = {}
            for _n in _entity_candidates:
                _nid = _n.get("node_id")
                if _nid and _nid not in _seen_node_ids:
                    _seen_node_ids[_nid] = _n
                elif not _nid:
                    # No ID — keep by name as fallback (shouldn't happen in practice)
                    _fallback_key = (_n.get("name") or "").lower().strip()
                    if _fallback_key and _fallback_key not in {
                        (x.get("name") or "").lower().strip()
                        for x in _seen_node_ids.values()
                    }:
                        _seen_node_ids[_fallback_key] = _n
            _unique_candidates = list(_seen_node_ids.values())

            _enrich_ids = [
                n.get("node_id") for n in _unique_candidates if n.get("node_id")
            ]
            if _enrich_ids:
                try:
                    _qdrant_content = self._qdrant.get_nodes_content_by_ids(
                        _enrich_ids
                    )
                    for _n in _unique_candidates:
                        _nid = _n.get("node_id")
                        if _nid and _nid in _qdrant_content:
                            _c = _qdrant_content[_nid]
                            _n["description"] = _c.get("description", "")
                            _n["summary"] = _c.get("description", "")
                            _n["isolated_contexts"] = _c.get(
                                "isolated_contexts", []
                            )
                except Exception as _e:  # pylint: disable=broad-exception-caught
                    logger.debug(
                        f"  [Entity] Qdrant content enrichment failed: {_e}"
                    )
            return _unique_candidates

        async def _meili_branch() -> list[dict]:
            """STEP 1b: BM25 keyword search over query + keywords (parallel per term)."""
            _meili_queries = [query] + [
                t for t in query_keywords if t and t.lower() not in query.lower()
            ]
            _per_term = await asyncio.gather(
                *[self._search_meili_by_keyword(_ts_q) for _ts_q in _meili_queries]
            )
            _seen_ts: set[str] = set()
            _all_keyword_nodes: list[dict] = []
            for _kn in _per_term:
                for _n in _kn:
                    _key = (_n.get("name") or "").lower()
                    if _key not in _seen_ts:
                        _seen_ts.add(_key)
                        _all_keyword_nodes.append(_n)
            return _all_keyword_nodes

        async def _vector_branch() -> list[dict]:
            """STEP 2: semantic vector search over all Qdrant collections."""
            logger.info("  [Vector] Running vector search")
            # Qdrant is the vector source of truth.
            return await self._search_qdrant_multi_collection(
                full_vector,
                date_filter=query_date_filter,
                period_filter=query_period_filter,
            )

        t_parallel_start = time.perf_counter()
        _entity_res, _meili_res, _vector_res = await asyncio.gather(
            asyncio.to_thread(_entity_branch),
            _meili_branch(),
            _vector_branch(),
            return_exceptions=True,
        )
        if isinstance(_entity_res, BaseException):
            logger.warning(f"  [Entity] Name matching failed: {_entity_res}")
            _entity_res = []
        if isinstance(_meili_res, BaseException):
            logger.warning(f"  [Keyword] Meili BM25 search failed: {_meili_res}")
            _meili_res = []
        if isinstance(_vector_res, BaseException):
            logger.warning(f"  [Vector] Vector search failed: {_vector_res}")
            _vector_res = []
        logger.info(
            f"  [⏱️ Timing] Parallel entity/BM25/vector search: "
            f"{time.perf_counter() - t_parallel_start:.2f}s"
        )

        # ── Merge in the original precedence order: entity → BM25 → vector ──
        for _n in _entity_res:
            entity_nodes.append(_n)
            node_names_found.add((_n.get("name") or "").lower().strip())
        if entity_nodes:
            logger.info(
                f"  [Entity] Found {len(entity_nodes)} nodes by name: "
                f"{[n['name'] for n in entity_nodes]}"
            )

        if _meili_res:
            entity_nodes = self._merge_search_results(
                entity_nodes,
                _meili_res,
                node_names_found,
            )
            logger.info(
                f"  [Keyword] Added {len(_meili_res)} Meili BM25 matches"
            )

        try:
            vector_results = _vector_res
            if vector_results:
                logger.info(
                    f"  [Vector] Using Qdrant multi-collection search with {len(vector_results)} hits"
                )
            else:
                logger.info("  [Vector] Qdrant returned no hits")

            for vnode in vector_results:
                _vkey = (vnode["name"] or "").lower().strip()
                if _vkey not in node_names_found:
                    vnode["_source"] = "vector"
                    vector_nodes.append(vnode)
                    node_names_found.add(_vkey)

            # STEP 2.5: NAME VARIANT EXPANSION for vector-found person entities
            # Catches "Margaret Johnson" -> "Margaret Johnson-Williams" for
            # multi-hop queries. One batched Kuzu lookup for all vector names.
            vector_variant_nodes = []
            _vector_names = [
                vnode.get("name", "") for vnode in vector_nodes if vnode.get("name")
            ]
            _v_variant_map: dict[str, list[dict]] = {}
            if _vector_names:
                try:
                    _v_variant_map = await asyncio.to_thread(
                        self._graph.find_name_variants_batch, _vector_names
                    )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.debug(f"  [Variant] Batch lookup failed: {e}")
            for vnode in vector_nodes:  # Check top vector results
                vnode_name = vnode.get("name", "")
                for v in _v_variant_map.get(vnode_name.lower().strip(), []):
                    _vv_key = (v["name"] or "").lower().strip()
                    if (
                        _vv_key not in node_names_found
                        and _vv_key != vnode_name.lower().strip()
                    ):
                        v["_source"] = "vector_variant"
                        v["_variant_of"] = vnode_name
                        vector_variant_nodes.append(v)
                        node_names_found.add(_vv_key)

            if vector_variant_nodes:
                logger.info(
                    f"  [Vector] Found {len(vector_variant_nodes)} name variants from vector results: "
                    f"{[(v['name'], v.get('_variant_of')) for v in vector_variant_nodes]}"
                )
                vector_nodes.extend(vector_variant_nodes)

            if vector_nodes:
                logger.info(
                    f"  [Vector] Found {len(vector_nodes)} semantically similar nodes: "
                    f"{[n['name'] for n in vector_nodes]}"
                )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(f"  [Vector] Vector merge failed: {e}")

        # Combine all found nodes
        all_found_nodes = entity_nodes + vector_nodes
        all_node_names = [n["name"] for n in all_found_nodes]

        # STEP 3: Community nodes are indexed in Qdrant node_cores just like entity
        # nodes and flow through section B of the candidate list via normal vector
        # search. No separate community search path is needed.

        # STEP 4: Minimal note grounding (only fetch for linked evidence, not chunking)
        # We don't chunk notes anymore - node summaries ARE the distilled knowledge
        # Build a mapping from node name -> linked notes for reference traceability
        # Use lowercase keys for case-insensitive lookup
        node_to_notes: dict[str, list[dict]] = {}
        grounding_note_ids = set()
        if all_node_names:
            _grounding_id_to_name: dict[str, str] = {}
            for _grounding_node in all_found_nodes:
                _gnid = _grounding_node.get("node_id") or _grounding_node.get("id")
                _gnname = (_grounding_node.get("name") or "").lower().strip()
                if _gnid and _gnname:
                    _grounding_id_to_name[_gnid] = _gnname

            if _grounding_id_to_name:
                evidence_results = self._graph.get_linked_evidence_by_node_ids(
                    list(_grounding_id_to_name.keys()),
                    limit_per_node=2,
                    node_id_to_name=_grounding_id_to_name,
                )
            else:
                evidence_results = self._graph.get_linked_evidence(
                    all_node_names, limit_per_node=2
                )
            for row in evidence_results:
                node_name = row.get("node_name")
                evidence = row.get("evidence", [])
                if node_name and evidence:
                    # Store with lowercase key for case-insensitive lookup
                    node_to_notes[node_name.lower()] = evidence
                for note in evidence:
                    grounding_note_ids.add(note["id"])
            logger.info(
                f"  [Retrieval] Found {len(grounding_note_ids)} grounding notes (for source links)"
            )
            # Debug: log the node→notes mapping
            logger.debug(
                f"  [Retrieval] node_to_notes keys: {list(node_to_notes.keys())}"
            )

        t_candidates = time.perf_counter() - t_phase_start
        logger.info(
            f"  [⏱️ Timing] Retrieval phase: {t_candidates:.2f}s "
            f"({len(entity_nodes)} entity + {len(vector_nodes)} vector nodes)"
        )

        # ============ PREPARE CANDIDATES (NODE SUMMARIES FIRST) ============
        # Node summaries are PRIMARY evidence - they contain distilled, isolated knowledge
        candidates = []

        # A. Entity Nodes (highest priority — direct name matches from query)
        # ── Pre-fetch relationships for all candidate nodes in one pass ──────
        # _get_node_relationships makes a synchronous Kuzu call per node.
        # Batching here avoids N serial Kuzu round-trips in the loops below.
        # A. Entity nodes
        for node in entity_nodes:
            if not self._get_node_text(node):
                continue
            linked_notes = node_to_notes.get(node["name"].lower(), [])
            _formatted = self._build_node_text(node, [])
            candidates.append(
                {
                    "text": _formatted,
                    "_rerank_text": _formatted,
                    "type": "entity_match",
                    "original_obj": node,
                    "linked_notes": linked_notes,
                }
            )

        # B. Vector nodes
        for node in vector_nodes:
            if not self._get_node_text(node):
                continue
            linked_notes = node_to_notes.get(node["name"].lower(), [])
            _result_type = (
                "community_summary"
                if (node.get("entity_type") or "").lower() == "community"
                else "vector_match"
            )
            _formatted = self._build_node_text(node, [])
            candidates.append(
                {
                    "text": _formatted,
                    "_rerank_text": _formatted,
                    "type": _result_type,
                    "original_obj": node,
                    "linked_notes": linked_notes,
                }
            )

        logger.info(
            f"  [Retrieval] Prepared {len(candidates)} candidates "
            f"({len(entity_nodes)} entity + {len(vector_nodes)} vector)"
        )

        # ============ RERANKING ============
        # Happens inside hybrid_search so every caller gets ranked results.
        # The reranker query is augmented with question_attribute and
        # expected_entity_types; candidate texts are prefixed with their actual
        # entity_type so the cross-encoder judges type alignment itself.
        if not candidates:
            logger.warning("  [Retrieval] No candidates found")
            return []

        combined_results = candidates

        t_ranking_start = time.perf_counter()
        combined_results = await self._apply_reranker_logging(
            query,
            combined_results,
            top_n=settings.RERANKER_TOP_K,
            score_threshold=settings.RERANKER_SCORE_THRESHOLD,
            question_attribute=question_attribute,
            expected_entity_types=expected_entity_types,
        )
        t_ranking = time.perf_counter() - t_ranking_start

        # Log what we're sending to LLM
        logger.info(
            f"  [Retrieval] Top {len(combined_results)} results by source: "
            f"entity={sum(1 for c in combined_results if c.get('type') == 'entity_match')}, "
            f"vector={sum(1 for c in combined_results if c.get('type') == 'vector_match')}, "
            f"neighbor={sum(1 for c in combined_results if c.get('type') == 'neighbor_node')}, "
            f"community={sum(1 for c in combined_results if c.get('type') == 'community_summary')}"
        )
        for i, doc in enumerate(combined_results):
            name = doc.get("original_obj", {}).get("name", "N/A")
            dtype = doc.get("type", "unknown")
            score = doc.get("rerank_score", 0.0)
            logger.info(f"    {i+1}. [{dtype}] {name} (rerank={score:.4f})")

        # Detailed logging to file (full summaries, not truncated)
        self._log_retrieval_details(query, combined_results, query_entities)

        t_total = time.perf_counter() - t_start
        logger.info(
            f"  [⏱️ Timing] Total: {t_total:.2f}s (Embed: {t_embedding:.2f}s | "
            f"Retrieval: {t_candidates:.2f}s | Rerank: {t_ranking:.2f}s)"
        )

        return combined_results

    async def _apply_reranker_logging(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches
        self,
        query: str,
        candidates: list[dict],
        top_n: int | None = None,
        question_attribute: str | None = None,
        expected_entity_types: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict]:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Rank candidates and return the top_n highest-scoring ones.

        Scores with the local reranker (``settings.MODEL_RERANKER_LOCAL``).
        The reranker is mandatory: a missing GGUF, a load failure or a run that
        returns no scores raises, and the chat job reports that error.

        Args:
            top_n: After ranking, slice to this many results.  None = no cutoff.
            score_threshold: After top_n slice, drop candidates scoring below
                this value.  None = no threshold applied.
            question_attribute: If provided, append to the reranker query so the
                model scores candidates against the specific attribute sought.
            expected_entity_types: If provided, append to the reranker query so
                the model knows what kind of entity the answer should be. Each
                candidate's actual entity_type is also prepended to its text so
                the reranker can judge type alignment without hard-filtering.
        """
        if not candidates:
            return []

        # Build the reranker query: append question_attribute and expected
        # entity types so the cross-encoder scores candidates against both
        # the specific fact sought and the kind of entity that should hold it.
        rerank_query = query
        _query_hints: list[str] = []
        if question_attribute:
            _query_hints.append(question_attribute)
        if expected_entity_types:
            _query_hints.append("type: " + ", ".join(expected_entity_types))
        if _query_hints:
            rerank_query = f"{query} [{'; '.join(_query_hints)}]"

        # Build candidate texts for the reranker.
        # _rerank_text is pre-built in _build_node_text format:
        #   "{name} is a {type}. {context}"
        #   "{origin} is a {type}. {ctx}\n{link}. {neighbour} is a {type}. {ctx}"
        # Fall back to _build_node_text(original_obj, []) when not pre-set.
        texts: list[str] = []
        for candidate in candidates:
            text = (candidate.get("_rerank_text") or "").strip()
            if not text:
                text = self._build_node_text(candidate.get("original_obj") or {}, [])
            if not text:
                text = (candidate.get("text") or "").strip()
            texts.append(text)

        from app.services.reranker import reranker_service

        results = await reranker_service.rerank(rerank_query, texts)
        model_scores = {
            r["index"]: float(r.get("relevance_score") or 0.0)
            for r in results
            if "index" in r
        }
        if not model_scores:
            raise RuntimeError("Reranker returned no scores")
        logger.info(
            f"  [Reranker] {settings.MODEL_RERANKER_LOCAL} scored {len(model_scores)} "
            f"candidates (top score: {max(model_scores.values()):.4f})"
        )

        for idx, candidate in enumerate(candidates):
            rerank_score = model_scores.get(idx, 0.0)
            candidate["rerank_score"] = rerank_score
            candidate["reranker_rank"] = idx + 1
            _name = (candidate.get("original_obj") or {}).get("name", "?")
            _preview = texts[idx].replace("\n", " ")
            logger.debug(
                f"  [Reranker] [{idx + 1}] {_name} rerank={rerank_score:.4f} | text: {_preview!r}"
            )

        candidates.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)

        if top_n is not None and len(candidates) > top_n:
            logger.info(
                f"  [Reranker] Ranked {len(candidates)} candidates → keeping top {top_n}"
            )
            candidates = candidates[:top_n]
        else:
            logger.info(f"  [Reranker] Ranked {len(candidates)} candidates (no cutoff)")

        if score_threshold is not None:
            before = len(candidates)
            candidates = [
                c for c in candidates if c.get("rerank_score", 0.0) >= score_threshold
            ]
            if len(candidates) < before:
                logger.info(
                    f"  [Reranker] Score threshold {score_threshold} dropped "
                    f"{before - len(candidates)} candidate(s) → {len(candidates)} remaining"
                )

        return candidates

    async def retrieve_with_iterative_loop(  # pylint: disable=too-many-nested-blocks,too-many-locals,too-many-branches,too-many-statements,unused-argument
        self,
        query: str,
        top_k: int = 50,
        progress_callback: Callable[[str, str | None], None] | None = None,
        conversation_history: list[dict] | None = None,
    ) -> tuple[str | None, list[dict], str | None]:
        """
        Iterative retrieval loop — one LLM call per iteration.

        Each iteration the LLM either:
          - Provides a NEXT_QUERY to search for, or
          - Outputs ANSWER with the final bare answer phrase.

        Continues until ANSWER is produced or MAX_LOOP_ITERATIONS is hit.
        On exhaustion, synthesizes from accumulated steps using the existing
        final_synthesis_from_sub_results function.
        Returns (final_answer, all_accumulated_docs, thinking).
        """
        # This KB's LLM (a pinned per-KB model, or the system default).
        llm = self._llm

        def _progress(stage: str, model: str | None = None) -> None:
            if progress_callback:
                progress_callback(stage, model)

        conversation_context = ""
        if conversation_history:
            from app.schemas.chat import render_history

            rendered = render_history(conversation_history, 500)
            if rendered:
                conversation_context = "CONVERSATION SO FAR:\n" + rendered + "\n\n"

        _t_start = time.perf_counter()
        logger.info(
            f"\n{'='*70}\n"
            f"[IterLoop] START retrieve_with_iterative_loop\n"
            f"  query='{query}'\n"
            f"{'='*70}"
        )

        # Analyse the original question once to get question_attribute for
        # expansion reranking. hybrid_search runs its own analysis per sub-query
        # (type pre-filter + initial reranking). This call covers the expansion
        # docs which are reranked outside hybrid_search.
        _progress("Analyzing question", "Gemma4")
        _loop_qa = llm.analyze_query(query)
        _loop_question_attr = _loop_qa.get("question_attribute") or None
        logger.info(f"  [IterLoop] question_attribute={_loop_question_attr!r}")

        accumulated_steps: list[dict] = []
        all_docs: list[dict] = []
        surfaced_names: set[str] = set()
        current_query: str | None = None
        tried_queries: list[str] = []

        for iteration in range(settings.MAX_LOOP_ITERATIONS):
            _progress(
                f"Retrieval loop {iteration + 1}/{settings.MAX_LOOP_ITERATIONS}",
                None,
            )
            logger.info(
                f"  [IterLoop] Iteration {iteration + 1}/{settings.MAX_LOOP_ITERATIONS} "
                f"| current_query={current_query!r}"
            )

            # ── Retrieve docs (skip on first iteration) ───────────────────────
            if current_query is not None:
                _progress(
                    f"Searching knowledge base ({iteration + 1}/{settings.MAX_LOOP_ITERATIONS})",
                    "Embeddings + Reranker",
                )
                selected_docs = await self.hybrid_search(current_query)
                logger.info(
                    f"  [IterLoop] hybrid_search returned {len(selected_docs)} ranked candidates"
                )

                for d in selected_docs:
                    name = (d.get("original_obj") or {}).get("name") or d.get(
                        "name", ""
                    )
                    if name:
                        surfaced_names.add(name)

                # Graph expansion
                expanded: list[dict] = []
                try:
                    _progress("Expanding graph neighbors", None)
                    expanded = await self._expand_relevant_neighbors(
                        selected_docs, current_query, surfaced_names
                    )
                    logger.info(
                        f"  [IterLoop] Graph expansion: {len(expanded)} neighbors"
                    )
                    if expanded:
                        _progress("Reranking graph neighbors", settings.MODEL_RERANKER_LOCAL)
                        expanded = await self._apply_reranker_logging(
                            current_query,
                            expanded,
                            top_n=settings.RERANKER_TOP_K,
                            question_attribute=_loop_question_attr,
                        )
                        for d in expanded:
                            name = (d.get("original_obj") or {}).get("name") or d.get(
                                "name", ""
                            )
                            if name:
                                surfaced_names.add(name)
                except Exception as _exp_err:  # pylint: disable=broad-exception-caught
                    logger.warning(f"  [IterLoop] Graph expansion failed: {_exp_err}")

                docs = selected_docs + expanded

                # Accumulate unique docs into all_docs. A graph_expansion doc
                # carries its origin node's name, which selected_docs already
                # put here — fold its neighbour notes into that doc so they
                # stay citable in ``sources``.
                by_name = {
                    (d.get("original_obj") or {}).get("name") or d.get("name", ""): d
                    for d in all_docs
                }
                for d in docs:
                    name = (d.get("original_obj") or {}).get("name") or d.get(
                        "name", ""
                    )
                    kept = by_name.get(name)
                    if kept is None:
                        all_docs.append(d)
                        by_name[name] = d
                        continue
                    known = {n.get("id") for n in kept.get("linked_notes", [])}
                    kept["linked_notes"] = kept.get("linked_notes", []) + [
                        n for n in d.get("linked_notes", []) if n.get("id") not in known
                    ]
            else:
                docs = []

            # ── LLM iterative step ────────────────────────────────────────────
            _progress(
                f"Reasoning over retrieved context ({iteration + 1}/{settings.MAX_LOOP_ITERATIONS})",
                "Gemma4",
            )
            result = await llm.iterative_step(
                original_question=query,
                accumulated_steps=accumulated_steps,
                search_query=current_query,
                docs=docs,
                tried_queries=tried_queries if tried_queries else None,
                conversation_context=conversation_context or None,
            )

            logger.info(
                f"  [IterLoop] iterative_step result: can_answer={result['can_answer']} "
                f"final_answer={result.get('final_answer')!r} "
                f"next_query={result.get('next_query')!r}"
            )

            # Store step findings for the next iteration's context
            if docs and (result.get("full_answer") or result.get("reasoning")):
                accumulated_steps.append(
                    {
                        "query": current_query,
                        "full_answer": result.get("full_answer", ""),
                        "reasoning": result.get("reasoning", ""),
                    }
                )

            if result["can_answer"]:
                final_answer = result["final_answer"]
                thinking = result.get("thinking")
                _t_total = time.perf_counter() - _t_start
                logger.info(
                    f"\n{'='*70}\n"
                    f"[IterLoop] COMPLETE in {_t_total:.2f}s\n"
                    f"  iterations={iteration + 1}\n"
                    f"  answer='{final_answer}'\n"
                    f"  docs_accumulated={len(all_docs)}\n"
                    f"  thinking={'yes' if thinking else 'no'}\n"
                    f"{'='*70}"
                )
                return final_answer, all_docs, thinking

            # Mark this query as tried now that we have processed its results
            if current_query and current_query not in tried_queries:
                tried_queries.append(current_query)

            next_q = result.get("next_query")
            if not next_q:
                logger.warning(
                    f"  [IterLoop] No NEXT_QUERY at iteration {iteration + 1}, stopping"
                )
                break

            current_query = next_q

        # ── Loop exhausted: synthesize from accumulated steps ─────────────────
        _progress("Synthesizing final answer", "Gemma4")
        # Rather than returning None (which forces an expensive fallback re-run),
        # convert accumulated steps into sub_results for final_synthesis_from_sub_results.
        _t_total = time.perf_counter() - _t_start
        # Return the best finding from the last accumulated step.
        # The iterative loop already handles synthesis — no separate sub-results
        # pass is needed. If the loop ran out of iterations without a definitive
        # answer, the most recent FINDING is the best we have.
        last_answer: str | None = None
        for step in reversed(accumulated_steps):
            fa = step.get("full_answer", "").strip()
            if fa and fa.lower() not in {
                "not found",
                "none",
                "insufficient",
                "n/a",
                "unknown",
            }:
                last_answer = fa
                break

        logger.info(
            f"\n{'='*70}\n"
            f"[IterLoop] EXHAUSTED in {_t_total:.2f}s\n"
            f"  docs_accumulated={len(all_docs)}\n"
            f"  best_finding={last_answer!r}\n"
            f"{'='*70}"
        )
        return last_answer, all_docs, None


retrieval_service = RetrievalService()
