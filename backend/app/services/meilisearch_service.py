"""Meilisearch keyword / BM25 search service."""

from __future__ import annotations

import time
from typing import Any

import meilisearch
from meilisearch.errors import MeilisearchApiError
from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("MeilisearchService")

_SEARCHABLE = [
    "name",
    "type",
    "isolated_contexts",
    "relationship_natural_language",
]
_FILTERABLE = ["type", "community_level"]


class MeilisearchService:
    """Meilisearch client managing per-KB node indexes for keyword search."""

    def __init__(self, collection_name: str | None = None) -> None:
        # Keep attribute name `collection` for call-site compatibility
        self.collection = collection_name or settings.MEILI_INDEX_NAME
        self._client = None
        self._retry_at = 0.0
        self._connect()

    @property
    def client(self):
        """(Re)connect lazily: the desktop runtime boots Meilisearch after the API is up."""
        if self._client is None and time.monotonic() >= self._retry_at:
            self._connect()
        return self._client

    @property
    def _enabled(self) -> bool:
        return self._client is not None

    def _connect(self) -> None:
        try:
            client = meilisearch.Client(
                f"http://{settings.MEILI_HOST}:{settings.MEILI_PORT}", settings.MEILI_MASTER_KEY
            )
            client.health()
            self._client = client
            self._ensure_collection()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._client = None
            self._retry_at = time.monotonic() + 5
            logger.warning(f"Meilisearch not reachable yet (retrying on use): {exc}")

    def _index(self):
        return self.client.index(self.collection)

    def _ensure_collection(self) -> None:
        """Create the index if it does not already exist and apply settings."""
        try:
            self.client.get_index(self.collection)
        except MeilisearchApiError:
            try:
                task = self.client.create_index(
                    self.collection, {"primaryKey": "node_id"}
                )
                self.client.wait_for_task(task.task_uid, timeout_in_ms=10000)
                logger.info(f"[Meili] Created index '{self.collection}'")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(f"[Meili] Index creation failed: {exc}")
                return
        try:
            idx = self._index()
            task = idx.update_settings(
                {
                    "searchableAttributes": _SEARCHABLE,
                    "filterableAttributes": _FILTERABLE,
                    "displayedAttributes": ["*"],
                }
            )
            self.client.wait_for_task(task.task_uid, timeout_in_ms=10000)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"[Meili] Settings update: {exc}")

    def is_available(self) -> bool:
        if not self.client:
            return False
        try:
            self.client.health()
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    def reset_all(self) -> None:
        if not self.client:
            return
        try:
            task = self.client.delete_index(self.collection)
            self.client.wait_for_task(task.task_uid, timeout_in_ms=10000)
            logger.info(f"[Meili] Deleted index '{self.collection}'")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[Meili] Could not delete index '{self.collection}': {exc}")
        self._ensure_collection()
        logger.info(f"[Meili] Index '{self.collection}' reset.")

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Fetch one document by node id."""
        if not self.is_available():
            return None
        try:
            doc = self._index().get_document(node_id)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if doc is None:
            return None
        # meilisearch-python may return a Document model — normalize to a plain dict.
        if isinstance(doc, dict):
            return doc
        if hasattr(doc, "__dict__"):
            raw = {
                k: v
                for k, v in vars(doc).items()
                if not k.startswith("_")
            }
            if raw:
                return raw
        try:
            return dict(doc)  # type: ignore[arg-type]
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        # Last resort: attribute access for known fields
        out: dict[str, Any] = {}
        for key in (
            "node_id",
            "name",
            "type",
            "isolated_contexts",
            "relationship_natural_language",
            "community_level",
        ):
            try:
                val = getattr(doc, key)
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if val is not None:
                out[key] = val
        return out or None

    def search_nodes(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        try:
            response = self._index().search(query, {"limit": limit})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Meilisearch search failed: {exc}")
            return []

        hits: list[dict[str, Any]] = []
        docs = response.get("hits", [])
        total = len(docs)
        for idx, document in enumerate(docs):
            score = float(total - idx)
            hits.append({"score": score, "payload": document})
        return hits

    def index_node(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        node_id: str,
        name: str,
        node_type: str,
        isolated_contexts_text: str = "",
        relationship_natural_language: str = "",
        community_level: int | None = None,
    ) -> None:
        if not self.is_available():
            return
        doc: dict[str, Any] = {
            "node_id": node_id,
            "name": name,
            "type": node_type,
        }
        if isolated_contexts_text:
            doc["isolated_contexts"] = isolated_contexts_text
        if relationship_natural_language:
            doc["relationship_natural_language"] = relationship_natural_language
        if community_level is not None:
            doc["community_level"] = community_level
        try:
            task = self._index().add_documents([doc], primary_key="node_id")
            self.client.wait_for_task(task.task_uid, timeout_in_ms=5000)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Meili index_node failed for {node_id}: {exc}")

    def update_node_community(
        self,
        node_id: str,
        relationship_natural_language: str = "",
        name: str = "",
    ) -> None:
        self.update_nodes_community(
            [
                {
                    "node_id": node_id,
                    "relationship_natural_language": relationship_natural_language,
                    "name": name,
                }
            ]
        )

    def update_nodes_community(self, rows: list[dict]) -> None:
        """Batch community-field refresh: one add_documents call (and one task
        wait) for the whole set, instead of one HTTP write per node."""
        if not self.is_available() or not rows:
            return
        docs: list[dict[str, Any]] = []
        for row in rows:
            node_id = row["node_id"]
            existing = self.get_node(node_id) or {"node_id": node_id}
            if row.get("name"):
                existing["name"] = row["name"]
            if row.get("relationship_natural_language"):
                existing["relationship_natural_language"] = row[
                    "relationship_natural_language"
                ]
            existing["node_id"] = node_id
            docs.append(existing)
        try:
            task = self._index().add_documents(docs, primary_key="node_id")
            self.client.wait_for_task(task.task_uid, timeout_in_ms=30000)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(
                f"Meili update_nodes_community failed for {len(docs)} node(s): {exc}"
            )

    def delete_node(self, node_id: str) -> None:
        if not self.is_available():
            return
        try:
            task = self._index().delete_document(node_id)
            self.client.wait_for_task(task.task_uid, timeout_in_ms=5000)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if "404" in str(exc) or "not found" in str(exc).lower():
                return
            logger.warning(f"Meili delete_node failed for {node_id}: {exc}")


meilisearch_service = MeilisearchService()
