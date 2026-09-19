"""Qdrant vector database service for node, relationship, and context storage and retrieval."""

# pylint: disable=wrong-import-order
from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.log import get_logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

logger = get_logger("QdrantService")


# Points per upsert request. A 2560-dim vector is ~27 KB of REST JSON, so this
# keeps a request near 3 MB — well inside Qdrant's limit, with headroom for
# larger embedding models. Override with ORB_QDRANT_UPSERT_BATCH.
_UPSERT_BATCH_SIZE = max(1, int(os.environ.get("ORB_QDRANT_UPSERT_BATCH", "128")))


class QdrantService:
    """Qdrant vector store managing node-core, relationship, and isolated-context collections."""

    def __init__(
        self,
        col_cores: str | None = None,
        col_relationships: str | None = None,
        col_contexts: str | None = None,
    ) -> None:
        self._col_cores = col_cores or settings.QDRANT_COLLECTION_NODE_CORES
        self._col_rels = (
            col_relationships or settings.QDRANT_COLLECTION_NODE_RELATIONSHIPS
        )
        self._col_contexts = (
            col_contexts or settings.QDRANT_COLLECTION_NODE_ISOLATED_CONTEXTS
        )
        self._client: QdrantClient | None = None
        self._retry_at = 0.0
        self._connect()

    @property
    def client(self) -> QdrantClient | None:
        """(Re)connect lazily: the desktop runtime boots Qdrant after the API is up."""
        if self._client is None and time.monotonic() >= self._retry_at:
            self._connect()
        return self._client

    @property
    def _enabled(self) -> bool:
        return self._client is not None

    def _connect(self) -> None:
        try:
            client = QdrantClient(
                host=settings.QDRANT_HOST,
                port=settings.QDRANT_PORT,
                api_key=settings.QDRANT_API_KEY,
            )
            client.get_collections()  # the constructor never touches the network
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._client = None
            self._retry_at = time.monotonic() + 5
            logger.warning(f"Qdrant not reachable yet (retrying on use): {exc}")
            return
        self._client = client
        # Prefer the selected local embed model's dims before creating collections.
        try:
            from app.services.local_models import load_manifest

            dims = (load_manifest().get("selection") or {}).get("embedding_dims")
            if dims:
                settings.EMBEDDING_DIMENSIONS = int(dims)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        # Ensure all three collections exist for this KB (idempotent).
        self._ensure_collections()

    def _ensure_collections(self) -> None:
        """Create any missing Qdrant collections for this KB instance (idempotent).

        Empty collections with a dim mismatch are safe to drop and recreate —
        but only when **no** sibling collection has data at the old dim
        (avoids split-brain cores@1024 + contexts@768).

        Non-empty mismatched collections block all recreate/create for this set.
        Mid-ingest upsert paths fail closed via ``_prepare_vector``.
        """
        try:
            existing = {c.name for c in self.client.get_collections().collections}
            want_size = int(settings.EMBEDDING_DIMENSIONS) or 1024
            names = (self._col_cores, self._col_rels, self._col_contexts)

            nonempty_mismatch: list[tuple[str, int, int]] = []
            empty_mismatch: list[str] = []
            for name in names:
                if name not in existing:
                    continue
                try:
                    info = self.client.get_collection(name)
                    params = getattr(info.config, "params", None)
                    vectors = getattr(params, "vectors", None) if params else None
                    size = getattr(vectors, "size", None) if vectors else None
                    if isinstance(vectors, dict):
                        size = vectors.get("size")
                    if size is None or int(size) == want_size:
                        continue
                    points = int(getattr(info, "points_count", 0) or 0)
                    if points > 0:
                        nonempty_mismatch.append((name, int(size), points))
                    else:
                        empty_mismatch.append(name)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "[Qdrant] Could not inspect collection '%s': %s", name, exc
                    )

            if nonempty_mismatch:
                for name, size, points in nonempty_mismatch:
                    logger.error(
                        "[Qdrant] Collection '%s' dim=%s but EMBEDDING_DIMENSIONS=%s "
                        "with %s points — refusing to wipe or recreate siblings. "
                        "Rebuild via admin reset or clear indexes before changing embed model.",
                        name,
                        size,
                        want_size,
                        points,
                    )
                return

            for name in empty_mismatch:
                logger.warning(
                    "[Qdrant] Empty collection '%s' dim mismatch "
                    "(want=%s) — recreating.",
                    name,
                    want_size,
                )
                self.client.delete_collection(name)
                existing.discard(name)

            for name in names:
                if name not in existing:
                    self.client.create_collection(
                        collection_name=name,
                        vectors_config=VectorParams(
                            size=want_size,
                            distance=Distance.COSINE,
                        ),
                    )
                    logger.info(
                        "[Qdrant] Created collection '%s' (dim=%s)", name, want_size
                    )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[Qdrant] Could not ensure collections: {exc}")

    def ensure_vector_size(self, vector_len: int) -> None:
        """Adopt ``vector_len`` as EMBEDDING_DIMENSIONS and recreate mismatched collections.

        Always re-checks collection sizes — even when settings already match — because
        collections may still be at an older dimension after a model upgrade.
        """
        if not vector_len or not self.is_available():
            return
        vector_len = int(vector_len)
        if int(settings.EMBEDDING_DIMENSIONS) != vector_len:
            logger.warning(
                "[Qdrant] Updating EMBEDDING_DIMENSIONS %s → %s from live embedding vector",
                settings.EMBEDDING_DIMENSIONS,
                vector_len,
            )
            settings.EMBEDDING_DIMENSIONS = vector_len
        self._ensure_collections()

    #: Why the last batched upsert failed, for callers that must abort loudly.
    _last_upsert_error: str | None = None

    def _upsert_batched(self, collection_name: str, points: list) -> None:
        """Upsert in chunks, because one request has a size ceiling.

        A point with a 2560-dim vector is ~27 KB as REST JSON, so a few hundred
        of them exceed Qdrant's request limit and the whole call is rejected
        with 400 — losing every point in it, not just the overflow. A note
        producing 713 new entities failed exactly this way, leaving nodes in
        Kuzu with no Qdrant counterpart.

        Failures name the batch, so a partial write says how far it got.
        """
        size = _UPSERT_BATCH_SIZE
        for start in range(0, len(points), size):
            chunk = points[start : start + size]
            try:
                self.client.upsert(collection_name=collection_name, points=chunk)
            except Exception as exc:
                raise RuntimeError(
                    f"batch {start // size + 1} of "
                    f"{(len(points) + size - 1) // size} "
                    f"({len(chunk)} of {len(points)} points): {exc}"
                ) from exc

    def _prepare_vector(self, vector: list[float] | None) -> list[float] | None:
        """Validate embedding dims before upsert — never recreate collections mid-ingest.

        Collection create/resize belongs in ``sync_embedding_infrastructure`` /
        ``ensure_vector_size`` at startup or model change, not on every point write.
        """
        if not vector:
            return vector
        want = int(settings.EMBEDDING_DIMENSIONS) or 1024
        got = len(vector)
        if got != want:
            raise ValueError(
                f"Embedding dim {got} != EMBEDDING_DIMENSIONS {want}. "
                "Fix the selected embed model / re-sync infrastructure before ingesting."
            )
        return vector

    @property
    def col_contexts(self) -> str:
        """Name of the isolated-contexts collection for this KB instance."""
        return self._col_contexts

    @property
    def collections(self) -> list[str]:
        """Return the list of Qdrant collection names searched during retrieval."""
        # node_cores is included in vector search when nodes have a merged-context
        # vector (written by _update_node_summary). The merged vector embeds all
        # accumulated isolated contexts as a single passage, enabling multi-constraint
        # queries to match whole-node content rather than one sentence at a time.
        # Nodes without a merged vector simply don't appear in search results.
        return [self._col_cores, self._col_rels, self._col_contexts]

    def is_available(self) -> bool:
        """Return True if Qdrant is reachable and the service is enabled."""
        if not self.client:
            return False
        try:
            self.client.get_collections()
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    def reset_all(self) -> None:
        """Delete and recreate all Qdrant collections for this KB, wiping all vectors."""
        if not self.client:
            return
        for name in (self._col_cores, self._col_rels, self._col_contexts):
            try:
                self.client.delete_collection(name)
                logger.info(f"[Qdrant] Deleted collection '{name}'")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(f"[Qdrant] Could not delete collection '{name}': {exc}")
        self._ensure_collections()
        logger.info("[Qdrant] All collections reset.")

    def search_all_collections(
        self,
        query_vector: list[float],
        limit: int,
        min_score: float,
        contexts_filter: Filter | None = None,
        period_key_filter: str | None = None,
        day_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Search all Qdrant collections and merge the resulting scored hits."""
        if not self.is_available() or not self.client:
            return []

        def _search_one(collection: str) -> list[dict[str, Any]]:
            query_filter = None
            if collection == self._col_contexts and contexts_filter is not None:
                query_filter = contexts_filter
            elif collection == self._col_cores and period_key_filter:
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="period_key",
                            match=MatchValue(value=period_key_filter),
                        )
                    ]
                )

            try:
                result = self.client.query_points(
                    collection_name=collection,
                    query=query_vector,
                    limit=limit,
                    score_threshold=min_score,
                    query_filter=query_filter,
                    with_payload=True,
                )
                return [
                    {
                        "collection": collection,
                        "score": point.score,
                        "payload": point.payload or {},
                    }
                    for point in result.points
                ]
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Qdrant search failed for {collection}: {exc}")
                return []

        # ponytail: sequential over 3 local collections; fan out with a thread pool
        # if Qdrant ever moves off localhost.
        hits: list[dict[str, Any]] = []
        for collection in self.collections:
            if day_only and collection != self._col_contexts:
                continue
            hits.extend(_search_one(collection))
        return hits

    def search_node_cores(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        query_vector: list[float],
        limit: int,
        min_score: float,
        node_type: str | None = None,
        community_level: int | None = None,
    ) -> list[
        dict[str, Any]
    ]:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Search the node-cores collection with optional type and community-level filters."""
        if not self.is_available() or not self.client:
            return []

        must = []
        if node_type is not None:
            must.append(FieldCondition(key="type", match=MatchValue(value=node_type)))
        if community_level is not None:
            must.append(
                FieldCondition(
                    key="community_level", match=MatchValue(value=community_level)
                )
            )

        query_filter = Filter(must=must) if must else None
        try:
            result = self.client.query_points(
                collection_name=self._col_cores,
                query=query_vector,
                limit=limit,
                score_threshold=min_score,
                query_filter=query_filter,
                with_payload=True,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Qdrant node core search failed: {exc}")
            return []

        return [
            {"score": point.score, "payload": point.payload or {}}
            for point in result.points
        ]

    # ── Write helpers ──────────────────────────────────────────────────────────

    def upsert_node_core(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        node_id: str,
        name: str,
        node_type: str,
        description_vector: list[float],
        description: str = "",
        community_level: int | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> bool:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Upsert one point in node_cores (one point per node).

        ``community_level`` is set only for community nodes (the level they ARE at).
        Regular nodes carry no community fields here — membership is expressed via
        relationships in node_relationships.

        Returns True when the point was stored successfully.
        """
        if not self.is_available() or not self.client:
            return False
        description_vector = self._prepare_vector(description_vector) or description_vector
        collection = self._col_cores
        payload: dict[str, Any] = {
            "node_id": node_id,
            "name": name,
            "type": node_type,
        }
        if description:
            payload["description"] = description
        if community_level is not None:
            payload["community_level"] = community_level
        if extra_payload:
            payload.update(extra_payload)
        try:
            self.client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_OID, node_id)),
                        vector=description_vector,
                        payload=payload,
                    )
                ],
            )
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Fail closed — never call ensure_vector_size mid-ingest (split-brain risk).
            logger.warning(f"Qdrant upsert_node_core failed for {node_id}: {exc}")
            return False

    def upsert_node_cores(self, cores: list[dict[str, Any]]) -> bool:
        """Batch-upsert node_cores points in a single Qdrant call.

        Each entry takes the same fields as ``upsert_node_core``:
        node_id, name, node_type, description_vector, and optional description /
        community_level / extra_payload. Returns True when all points stored.
        """
        if not cores:
            return True
        if not self.is_available() or not self.client:
            return False
        points: list[PointStruct] = []
        for core in cores:
            vector = self._prepare_vector(core["description_vector"])
            payload: dict[str, Any] = {
                "node_id": core["node_id"],
                "name": core["name"],
                "type": core["node_type"],
            }
            if core.get("description"):
                payload["description"] = core["description"]
            if core.get("community_level") is not None:
                payload["community_level"] = core["community_level"]
            if core.get("extra_payload"):
                payload.update(core["extra_payload"])
            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_OID, core["node_id"])),
                    vector=vector,
                    payload=payload,
                )
            )
        try:
            self._upsert_batched(self._col_cores, points)
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Qdrant upsert_node_cores failed for %d point(s): %s", len(points), exc
            )
            self._last_upsert_error = str(exc)
            return False

    def upsert_node_relationships(self, rels: list[dict[str, Any]]) -> None:
        """Batch-upsert node_relationships points in a single Qdrant call.

        Each entry takes the same fields as ``upsert_node_relationship``:
        relationship_id, natural_language, nl_vector, source_node_id,
        target_node_id, and optional is_community_rel.
        """
        if not rels or not self.is_available() or not self.client:
            return
        points: list[PointStruct] = []
        for rel in rels:
            vector = self._prepare_vector(rel["nl_vector"])
            payload: dict[str, Any] = {
                "natural_language": rel["natural_language"],
                "source_node_id": rel["source_node_id"],
                "target_node_id": rel["target_node_id"],
            }
            if rel.get("is_community_rel"):
                payload["is_community_rel"] = True
            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_OID, rel["relationship_id"])),
                    vector=vector,
                    payload=payload,
                )
            )
        try:
            self._upsert_batched(self._col_rels, points)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Qdrant upsert_node_relationships failed for %d point(s): %s",
                len(points),
                exc,
            )

    def upsert_node_items(
        self,
        collection_name: str,
        node_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace all points for a node in a sub-item collection.

        Each item dict must have:
          - ``content`` (str): the text to embed / store
          - ``vector`` (list[float]): pre-computed embedding
          - Any extra payload fields are forwarded as-is.

        The method first deletes all existing points with ``parent_node_id == node_id``
        then inserts fresh points so the collection always reflects current state.
        """
        if not self.is_available() or not self.client:
            return
        # Validate dims before mutating — dim mismatch must not be swallowed.
        prepared: list[tuple[list[float], dict[str, Any]]] = []
        for item in items or []:
            vector = item.get("vector")
            if not vector:
                continue
            vector = self._prepare_vector(vector) or vector
            payload: dict[str, Any] = {"parent_node_id": node_id}
            for k, v in item.items():
                if k != "vector":
                    payload[k] = v
            prepared.append((vector, payload))
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="parent_node_id",
                                match=MatchValue(value=node_id),
                            )
                        ]
                    )
                ),
            )
            if not prepared:
                return
            points = [
                PointStruct(id=str(uuid.uuid4()), vector=vector, payload=payload)
                for vector, payload in prepared
            ]
            self._upsert_batched(collection_name, points)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Qdrant upsert_node_items failed for %s/%s: %s",
                collection_name,
                node_id,
                exc,
            )

    def append_node_item(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        collection_name: str,
        node_id: str,
        content: str,
        vector: list[float],
        note_created_at: str | None = None,
        note_id: str | None = None,
    ) -> bool:
        """Append a single new item to a sub-item collection without touching existing points.

        Used for isolated_contexts so we never re-embed prior contexts — only the new one.
        Returns True when the point was stored successfully.
        """
        if not self.is_available() or not self.client:
            return False
        vector = self._prepare_vector(vector) or vector
        point_id = str(uuid.uuid4())
        payload: dict[str, Any] = {"parent_node_id": node_id, "content": content}
        if note_id:
            # Which note said it — so re-ingesting or deleting that note can
            # take its contexts back out instead of piling new ones on top.
            payload["note_id"] = note_id
        if note_created_at:
            payload["note_created_at"] = note_created_at
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        try:
            self.client.upsert(collection_name=collection_name, points=[point])
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"Qdrant append_node_item failed for {collection_name}/{node_id}: {exc}"
            )
            return False

    def upsert_node_relationship(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        relationship_id: str,
        natural_language: str,
        nl_vector: list[float],
        source_node_id: str,
        target_node_id: str,
        is_community_rel: bool = False,
    ) -> None:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Upsert one point in node_relationships.

        ``is_community_rel`` marks membership relationships created during Leiden
        recomputation so they can be bulk-deleted when communities are rebuilt.
        """
        if not self.is_available() or not self.client:
            return
        nl_vector = self._prepare_vector(nl_vector) or nl_vector
        collection = self._col_rels
        payload: dict[str, Any] = {
            "natural_language": natural_language,
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
        }
        if is_community_rel:
            payload["is_community_rel"] = True
        try:
            self.client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_OID, relationship_id)),
                        vector=nl_vector,
                        payload=payload,
                    )
                ],
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"Qdrant upsert_node_relationship failed for {relationship_id}: {exc}"
            )

    def get_node_content_by_id(self, node_id: str) -> dict | None:
        """Fetch all content for a node from Qdrant.

        Returns a dict with keys: node_id, name, type, description, community_id,
        community_level, facts (list), potential_questions (list), isolated_contexts (list).
        Returns None if the node has no core entry.
        """
        if not self.is_available() or not self.client:
            return None

        point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, node_id))
        try:
            cores = self.client.retrieve(
                collection_name=self._col_cores,
                ids=[point_id],
                with_payload=True,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(
                f"Qdrant get_node_content_by_id core failed for {node_id}: {exc}"
            )
            return None

        def _scroll_contents(collection: str) -> list[str]:
            try:
                scroll_filter = Filter(
                    must=[
                        FieldCondition(
                            key="parent_node_id", match=MatchValue(value=node_id)
                        )
                    ]
                )
                items: list[str] = []
                offset = None
                while True:
                    results, next_offset = self.client.scroll(
                        collection_name=collection,
                        scroll_filter=scroll_filter,
                        limit=100,
                        offset=offset,
                        with_payload=True,
                    )
                    for point in results:
                        payload = point.payload or {}
                        content = payload.get("content")
                        if content:
                            date = payload.get("note_created_at")
                            items.append(f"{content} - {date}" if date else content)
                    if next_offset is None:
                        break
                    offset = next_offset
                return items
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Qdrant scroll failed for {collection}/{node_id}: {exc}")
                return []

        if not cores:
            # No node_cores entry, but isolated_contexts may still exist in the
            # sub-item collection (e.g. node was written by _update_node_summary
            # which never calls upsert_node_core). Return what we can.
            isolated_only = _scroll_contents(self._col_contexts)
            if not isolated_only:
                return None
            return {
                "node_id": node_id,
                "name": "",
                "type": "",
                "description": "",
                "community_level": None,
                "isolated_contexts": isolated_only,
            }

        core_payload = cores[0].payload or {}

        return {
            "node_id": node_id,
            "name": core_payload.get("name", ""),
            "type": core_payload.get("type", ""),
            "description": core_payload.get("description", ""),
            "community_level": core_payload.get("community_level"),
            "isolated_contexts": _scroll_contents(self._col_contexts),
        }

    def strip_facts_prefixes(self) -> int:
        """One-time: drop the legacy ``FACTS: k=v. `` prefix from stored descriptions.

        Raises when Qdrant is unreachable so the caller does not mark it done.
        """
        fixed = 0
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self._col_cores,
                limit=500,
                offset=offset,
                with_payload=["description"],
                with_vectors=False,
            )
            for point in points:
                desc = (point.payload or {}).get("description") or ""
                if not desc.startswith("FACTS:"):
                    continue
                m = re.search(r"^FACTS:.*?[.]\s+(.*)", desc, re.DOTALL)
                self.client.set_payload(
                    collection_name=self._col_cores,
                    payload={"description": m.group(1).strip() if m else ""},
                    points=[point.id],
                )
                fixed += 1
            if offset is None:
                return fixed

    def get_nodes_content_by_ids(self, node_ids: list[str]) -> dict[str, dict]:
        """Bulk-fetch content for multiple nodes from Qdrant.

        Returns a dict mapping node_id → content dict (same shape as get_node_content_by_id).
        Nodes with no core entry are omitted from the result.
        """
        if not self.is_available() or not self.client or not node_ids:
            return {}

        point_ids = [str(uuid.uuid5(uuid.NAMESPACE_OID, nid)) for nid in node_ids]
        cores_by_nodeid: dict[str, dict] = {}
        try:
            cores = self.client.retrieve(
                collection_name=self._col_cores,
                ids=point_ids,
                with_payload=True,
            )
            for point in cores:
                payload = point.payload or {}
                nid = payload.get("node_id")
                if nid:
                    cores_by_nodeid[nid] = payload
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Qdrant get_nodes_content_by_ids cores failed: {exc}")

        def _bulk_scroll(collection: str) -> dict[str, list[str]]:
            try:
                scroll_filter = Filter(
                    must=[
                        FieldCondition(
                            key="parent_node_id", match=MatchAny(any=node_ids)
                        )
                    ]
                )
                result: dict[str, list[str]] = {}
                offset = None
                while True:
                    points, next_offset = self.client.scroll(
                        collection_name=collection,
                        scroll_filter=scroll_filter,
                        limit=500,
                        offset=offset,
                        with_payload=True,
                    )
                    for point in points:
                        payload = point.payload or {}
                        nid = payload.get("parent_node_id")
                        content = payload.get("content")
                        if nid and content:
                            date = payload.get("note_created_at")
                            entry = f"{content} - {date}" if date else content
                            result.setdefault(nid, []).append(entry)
                    if next_offset is None:
                        break
                    offset = next_offset
                return result
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Qdrant bulk scroll failed for {collection}: {exc}")
                return {}

        contexts_by_id = _bulk_scroll(self._col_contexts)

        result: dict[str, dict] = {}
        for nid in node_ids:
            core = cores_by_nodeid.get(nid)
            if not core and nid not in contexts_by_id:
                continue
            result[nid] = {
                "node_id": nid,
                "name": (core or {}).get("name", ""),
                "type": (core or {}).get("type", ""),
                "description": (core or {}).get("description", ""),
                "community_level": (core or {}).get("community_level"),
                "isolated_contexts": contexts_by_id.get(nid, []),
            }
        return result

    def list_all_community_payloads(self) -> list[dict]:
        """Return a lightweight list of all community node rows from Qdrant node_cores.

        Each row has ``community_id``, ``community_level``, ``name``, and ``description``.
        Used for 3D layout computation and community description backfill.
        """
        if not self.is_available() or not self.client:
            return []
        rows: list[dict] = []
        try:
            offset = None
            while True:
                results, next_offset = self.client.scroll(
                    collection_name=self._col_cores,
                    scroll_filter=Filter(
                        must=[
                            FieldCondition(
                                key="type", match=MatchValue(value="community")
                            )
                        ]
                    ),
                    limit=500,
                    offset=offset,
                    with_payload=True,
                )
                for point in results:
                    p = point.payload or {}
                    rows.append(
                        {
                            "community_id": p.get("node_id"),
                            "community_level": p.get("community_level"),
                            "name": p.get("name"),
                            "description": p.get("description"),
                        }
                    )
                if next_offset is None:
                    break
                offset = next_offset
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Qdrant list_all_community_payloads failed: {exc}")
        return rows

    def delete_community_relationships(self) -> None:
        """Bulk-delete all node_relationship points flagged is_community_rel=True.

        Called at the start of every Leiden recompute so stale membership sentences
        are cleared before new ones are written.
        """
        if not self.is_available() or not self.client:
            return
        try:
            self.client.delete(
                collection_name=self._col_rels,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="is_community_rel",
                                match=MatchValue(value=True),
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Qdrant delete_community_relationships failed: {exc}")

    def find_node_id_by_name(self, name: str) -> str | None:
        """Resolve a node name to its stable node_id via the node_cores collection.

        Names are stored lowercase in Qdrant (normalized during ingestion).
        Returns the node_id string or None if not found.
        """
        if not self.is_available() or not self.client or not name:
            return None
        try:
            results, _ = self.client.scroll(
                collection_name=self._col_cores,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="name",
                            match=MatchValue(value=name.lower().strip()),
                        )
                    ]
                ),
                limit=1,
                with_payload=True,
                with_vectors=False,
            )
            if results:
                return (results[0].payload or {}).get("node_id")
            return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"[Qdrant] find_node_id_by_name failed for '{name}': {exc}")
            return None

    def find_node_ids_by_names(self, names: list[str]) -> dict[str, str | None]:
        """Batch-resolve node names to stable node_ids in a single Qdrant scroll.

        Returns a dict mapping each normalized name to its node_id (or None if not found).
        Uses a single OR-filter scroll instead of one query per name.
        """
        if not self.is_available() or not self.client or not names:
            return {}
        normalized = [n.lower().strip() for n in names if n and n.strip()]
        if not normalized:
            return {}
        result_map: dict[str, str | None] = {n: None for n in normalized}
        try:
            # Page through all matches so duplicates do not starve out other names
            # under a fixed limit. Stop early once all requested names are resolved.
            _scroll_filter = Filter(
                must=[
                    FieldCondition(
                        key="name",
                        match=MatchAny(any=normalized),
                    )
                ]
            )
            offset = None
            while True:
                results, next_offset = self.client.scroll(
                    collection_name=self._col_cores,
                    scroll_filter=_scroll_filter,
                    limit=500,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in results:
                    payload = point.payload or {}
                    node_name = (payload.get("name") or "").lower().strip()
                    node_id = payload.get("node_id")
                    # Keep first non-empty mapping per name for deterministic behavior.
                    if (
                        node_name
                        and node_id
                        and node_name in result_map
                        and result_map[node_name] is None
                    ):
                        result_map[node_name] = node_id

                if all(v is not None for v in result_map.values()):
                    break
                if next_offset is None:
                    break
                offset = next_offset
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"[Qdrant] find_node_ids_by_names failed: {exc}")
        return result_map

    def get_relationships_for_node_ids(self, node_ids: list[str]) -> list[dict]:
        """Fetch all relationship points from node_relationships where source or target is in node_ids.

        Returns list of dicts: {natural_language, source_node_id, target_node_id,
        source_node_name, target_node_name}.
        """
        if not self.is_available() or not self.client or not node_ids:
            return []
        try:
            results: list[dict] = []
            for direction_key in ("source_node_id", "target_node_id"):
                scroll_filter = Filter(
                    must=[
                        FieldCondition(
                            key=direction_key,
                            match=MatchAny(any=node_ids),
                        )
                    ]
                )
                offset = None
                while True:
                    points, next_offset = self.client.scroll(
                        collection_name=self._col_rels,
                        scroll_filter=scroll_filter,
                        limit=500,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    for point in points:
                        payload = point.payload or {}
                        results.append(
                            {
                                "natural_language": payload.get("natural_language", ""),
                                "source_node_id": payload.get("source_node_id", ""),
                                "target_node_id": payload.get("target_node_id", ""),
                            }
                        )
                    if next_offset is None:
                        break
                    offset = next_offset
            # Deduplicate by natural_language
            seen: set[str] = set()
            unique: list[dict] = []
            for r in results:
                key = r["natural_language"]
                if key and key not in seen:
                    seen.add(key)
                    unique.append(r)
            return unique
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"[Qdrant] get_relationships_for_node_ids failed: {exc}")
            return []

    def delete_node(self, node_id: str) -> None:
        """Delete a node core and all child items keyed by parent_node_id."""
        if not self.is_available() or not self.client:
            return

        try:
            self.client.delete(
                collection_name=self._col_cores,
                points_selector=[str(uuid.uuid5(uuid.NAMESPACE_OID, node_id))],
            )
            for collection_name in (self._col_contexts,):
                self.client.delete(
                    collection_name=collection_name,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[
                                FieldCondition(
                                    key="parent_node_id",
                                    match=MatchValue(value=node_id),
                                )
                            ]
                        )
                    ),
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Qdrant delete_node failed for {node_id}: {exc}")

    def node_ids_for_note(self, note_id: str) -> set[str]:
        """Nodes holding a context that came from this note."""
        if not self.is_available() or not self.client or not note_id:
            return set()
        out: set[str] = set()
        offset = None
        try:
            while True:
                points, offset = self.client.scroll(
                    collection_name=self._col_contexts,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="note_id", match=MatchValue(value=note_id))]
                    ),
                    limit=500,
                    offset=offset,
                    with_payload=["parent_node_id"],
                    with_vectors=False,
                )
                for point in points:
                    pid = (point.payload or {}).get("parent_node_id")
                    if pid:
                        out.add(pid)
                if not points or offset is None:
                    break
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[Qdrant] node_ids_for_note failed: {exc}")
        return out

    def delete_note_contexts(self, note_id: str) -> None:
        """Drop every context this note contributed, on every node."""
        if not self.is_available() or not self.client or not note_id:
            return
        try:
            self.client.delete(
                collection_name=self._col_contexts,
                points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="note_id", match=MatchValue(value=note_id))])
                ),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Qdrant delete_note_contexts failed for {note_id}: {exc}")

    def scroll_all_isolated_contexts_with_dates(self) -> list[dict]:
        """Return payload dicts for every isolated_context point that has a note_created_at field.

        Used by the temporal digest builder to group contexts by time period.
        """
        if not self.is_available() or not self.client:
            return []
        results: list[dict] = []
        offset = None
        try:
            while True:
                points, next_offset = self.client.scroll(
                    collection_name=self._col_contexts,
                    limit=500,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    payload = point.payload or {}
                    if payload.get("note_created_at"):
                        results.append(payload)
                if not points or next_offset is None:
                    break
                offset = next_offset
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"[Qdrant] scroll_all_isolated_contexts_with_dates failed: {exc}"
            )
        return results


qdrant_service = QdrantService()
