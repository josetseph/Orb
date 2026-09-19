"""Graph service backed by Kuzu (embedded graph database).

Schema
------
Node table:
    Node(id STRING PRIMARY KEY, kind STRING, name STRING, type STRING,
         pos_x DOUBLE, pos_y DOUBLE, pos_z DOUBLE)

  kind values: 'note' | 'indexable' | 'community'

Relationship tables:
    REFERENCES(FROM Node TO Node, note_id STRING)
    MEMBER_OF(FROM Node TO Node, level INT64)
    CONTAINS(FROM Node TO Node)
    SEMANTIC_REL(FROM Node TO Node,
        rel_type STRING, confidence DOUBLE, strength DOUBLE,
        relevance DOUBLE, edge_weight DOUBLE, relationship_id STRING,
        ingested_at STRING, last_updated STRING, mention_count INT64,
        note_id STRING,
        is_similarity BOOLEAN, created_at STRING)

Cypher translation notes
------------------------
- MATCH (n:Indexable)       -> MATCH (n:Node) WHERE n.kind IN ['indexable','note']
- MATCH (n:Note)            -> MATCH (n:Node) WHERE n.kind = 'note'
- MATCH (n:Community)       -> MATCH (n:Node) WHERE n.kind = 'community'
- NOT n:Note AND NOT n:Community -> n.kind = 'indexable'
- labels(n)                 -> [n.kind]
- type(r) for semantic rels -> r.rel_type
- elementId(n)              -> n.id
- Dynamic :REL_TYPE         -> :SEMANTIC_REL with r.rel_type = $rel_type
- n =~ 'pattern'            -> regexp_matches(n, 'pattern')
"""

# pylint: disable=too-many-lines,import-outside-toplevel

import re
import threading
from pathlib import Path

import kuzu
from app.core.config import REPO_ROOT, settings
from app.core.log import get_logger
from app.services.qdrant_service import QdrantService, qdrant_service
logger = get_logger("GraphService")


def _strip_facts_prefix(text: str) -> str:
    """Strip the legacy 'FACTS: k=v | k=v. Prose...' prefix from stored descriptions."""
    if not text or not text.startswith("FACTS:"):
        return text
    m = re.search(r"^FACTS:.*?[.]\s+(.*)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_STMTS = [
    """CREATE NODE TABLE IF NOT EXISTS Node(
        id STRING,
        kind STRING,
        name STRING,
        type STRING,
        pos_x DOUBLE,
        pos_y DOUBLE,
        pos_z DOUBLE,
        PRIMARY KEY(id)
    )""",
    """CREATE REL TABLE IF NOT EXISTS REFERENCES(
        FROM Node TO Node,
        note_id STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS MEMBER_OF(
        FROM Node TO Node,
        level INT64
    )""",
    """CREATE REL TABLE IF NOT EXISTS CONTAINS(
        FROM Node TO Node
    )""",
    """CREATE REL TABLE IF NOT EXISTS SEMANTIC_REL(
        FROM Node TO Node,
        rel_type STRING,
        confidence DOUBLE,
        strength DOUBLE,
        relevance DOUBLE,
        edge_weight DOUBLE,
        relationship_id STRING,
        ingested_at STRING,
        last_updated STRING,
        mention_count INT64,
        note_id STRING,
        is_similarity BOOLEAN,
        created_at STRING
    )""",
]


class GraphService:
    """Kuzu-backed graph database service managing nodes, relationships, and community membership."""

    def __init__(self, db_path: str | None = None, qdrant: QdrantService | None = None):
        self._qdrant = qdrant or qdrant_service
        _db_path = Path(db_path or settings.KUZU_DB_PATH).expanduser()
        if not _db_path.is_absolute():
            _db_path = REPO_ROOT / _db_path
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_db_path(_db_path)

        self.db = kuzu.Database(str(_db_path))
        self.conn = kuzu.Connection(self.db)
        self._lock = threading.RLock()
        self._init_schema()

    def _migrate_legacy_db_path(self, db_path: Path) -> None:
        """Move legacy Kuzu files from data root into the dedicated kuzu folder."""
        legacy_db_path = REPO_ROOT / "data" / "kuzu_graph"
        if db_path == legacy_db_path or db_path.exists():
            return

        legacy_wal_path = Path(f"{legacy_db_path}.wal")
        target_wal_path = Path(f"{db_path}.wal")
        if not legacy_db_path.exists() and not legacy_wal_path.exists():
            return

        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            if legacy_db_path.exists() and not db_path.exists():
                legacy_db_path.rename(db_path)
            if legacy_wal_path.exists() and not target_wal_path.exists():
                legacy_wal_path.rename(target_wal_path)
            logger.info(f"[Graph] Migrated Kuzu data to '{db_path}'.")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[Graph] Legacy Kuzu migration skipped: {exc}")

    def _init_schema(self) -> None:
        """Create all tables if they do not exist."""
        for stmt in _SCHEMA_STMTS:
            try:
                with self._lock:
                    self.conn.execute(stmt)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if "already exist" not in str(exc).lower():
                    logger.warning(f"[Graph] Schema init: {exc}")

    def close(self) -> None:
        """Close the Kuzu database connection."""
        try:
            self.conn.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def resolve_node_id(self, name: str) -> str | None:
        """Resolve a node name to its stable node_id via Qdrant node_cores."""
        return self._qdrant.find_node_id_by_name(name)

    def execute_query(self, query: str, params: dict = None):
        """Execute a Cypher query and return results as a list of row dictionaries."""
        if params is None:
            params = {}
        with self._lock:
            try:
                result = self.conn.execute(query, params)
                columns = result.get_column_names()
                rows = []
                while result.has_next():
                    row_values = result.get_next()
                    rows.append(dict(zip(columns, row_values)))
                return rows
            except Exception as exc:
                logger.error(f"[Graph] Query Failed: {exc}")
                logger.error(f"  Query: {query}")
                logger.error(f"  Params: {params}")
                raise

    # ---- Node lookup --------------------------------------------------------

    def find_nodes_by_name(self, names: list[str], fuzzy: bool = True) -> list[dict]:
        """Find graph nodes whose names fuzzy-match any entry in the given list."""
        if not names:
            return []
        names_lower = [n.lower() for n in names]

        if fuzzy:
            query = """
                        UNWIND $names AS q
            MATCH (n:Node)
            WHERE n.kind IN ['indexable', 'note']
                            AND toLower(n.name) CONTAINS q
            RETURN DISTINCT
                n.id AS node_id,
                n.name AS name,
                [n.kind] AS labels,
                n.type AS entity_type,
                                q AS matched_query
            LIMIT 50
            """
        else:
            query = """
            MATCH (n:Node)
            WHERE n.kind IN ['indexable', 'note']
              AND toLower(n.name) IN $names
            RETURN DISTINCT
                n.id AS node_id,
                n.name AS name,
                [n.kind] AS labels,
                n.type AS entity_type,
                toLower(n.name) AS matched_query
            LIMIT 50
            """
        return self.execute_query(query, {"names": names_lower})

    def find_nodes_by_exact_names(self, names: list[str]) -> dict[str, str]:
        """Batch lookup of indexable nodes by exact lowercase name.

        Returns a name→id mapping.  If multiple nodes share a name (stale
        duplicates), the lexicographically smallest id is returned so the result
        is deterministic.  The caller should treat any returned id as the
        canonical one and avoid minting a fresh UUID.
        """
        if not names:
            return {}
        normalized = [n.lower().strip() for n in names if n and n.strip()]
        if not normalized:
            return {}
        rows = self.execute_query(
            "MATCH (n:Node) WHERE n.kind = 'indexable' AND toLower(n.name) IN $names "
            "RETURN toLower(n.name) AS name, n.id AS id",
            {"names": normalized},
        )
        name_to_id: dict[str, str] = {}
        for row in sorted(rows, key=lambda r: r.get("id", "")):
            nm = row.get("name")
            if nm and nm not in name_to_id:
                name_to_id[nm] = row["id"]
        return name_to_id

    def find_name_variants(self, base_name: str, limit: int = 5) -> list[dict]:
        """Return name variants for a node using regexp case-insensitive prefix matching."""
        base_lower = base_name.lower().strip()
        _SUFFIX_PAT = ".* (sr\\.?|jr\\.?|senior|junior|i|ii|iii|iv|v|vi)$"  # pylint: disable=invalid-name
        query = """
        MATCH (n:Node)
        WHERE n.kind IN ['indexable', 'note']
          AND (toLower(n.name) STARTS WITH ($base_name + ' ')
           OR (toLower(n.name) CONTAINS $base_name AND size(n.name) > size($base_name) + 2))
          AND NOT (
            (toLower(n.name) CONTAINS ' sr' AND toLower($base_name) CONTAINS ' jr')
            OR (toLower(n.name) CONTAINS ' jr' AND toLower($base_name) CONTAINS ' sr')
            OR (regexp_matches(toLower(n.name), $suffix_pat)
                AND NOT regexp_matches(toLower($base_name), $suffix_pat))
            OR (regexp_matches(toLower($base_name), $suffix_pat)
                AND NOT regexp_matches(toLower(n.name), $suffix_pat))
          )
        RETURN DISTINCT
            n.id AS node_id,
            n.name AS name,
            [n.kind] AS labels,
            n.type AS entity_type
        LIMIT $limit
        """
        return self.execute_query(
            query,
            {"base_name": base_lower, "limit": limit, "suffix_pat": _SUFFIX_PAT},
        )

    def find_name_variants_batch(
        self, base_names: list[str], limit_per_name: int = 5
    ) -> dict[str, list[dict]]:
        """Batched ``find_name_variants``: one UNWIND scan for many base names.

        Returns a mapping of lowercase base name → variant rows (same row shape
        as ``find_name_variants``), capped at ``limit_per_name`` per base name.
        """
        normalized = list(
            dict.fromkeys(n.lower().strip() for n in base_names if n and n.strip())
        )
        if not normalized:
            return {}
        _SUFFIX_PAT = ".* (sr\\.?|jr\\.?|senior|junior|i|ii|iii|iv|v|vi)$"  # pylint: disable=invalid-name
        query = """
        UNWIND $base_names AS base
        MATCH (n:Node)
        WHERE n.kind IN ['indexable', 'note']
          AND (toLower(n.name) STARTS WITH (base + ' ')
           OR (toLower(n.name) CONTAINS base AND size(n.name) > size(base) + 2))
          AND NOT (
            (toLower(n.name) CONTAINS ' sr' AND base CONTAINS ' jr')
            OR (toLower(n.name) CONTAINS ' jr' AND base CONTAINS ' sr')
            OR (regexp_matches(toLower(n.name), $suffix_pat)
                AND NOT regexp_matches(base, $suffix_pat))
            OR (regexp_matches(base, $suffix_pat)
                AND NOT regexp_matches(toLower(n.name), $suffix_pat))
          )
        RETURN DISTINCT
            base AS base_name,
            n.id AS node_id,
            n.name AS name,
            [n.kind] AS labels,
            n.type AS entity_type
        """
        rows = self.execute_query(
            query, {"base_names": normalized, "suffix_pat": _SUFFIX_PAT}
        )
        result: dict[str, list[dict]] = {}
        for row in rows:
            base = row.pop("base_name", "")
            if not base:
                continue
            bucket = result.setdefault(base, [])
            if len(bucket) < limit_per_name:
                bucket.append(row)
        return result

    def get_indexable_nodes_for_communities(self) -> list[dict]:
        """Return all indexable (non-community, non-note) nodes for community detection input."""
        query = """
        MATCH (n:Node)
        WHERE n.kind = 'indexable' AND n.id IS NOT NULL
        RETURN
            n.id AS node_id,
            n.name AS name,
            n.type AS type
        """
        return self.execute_query(query, {})

    def wipe_all_nodes(self) -> int:
        """Delete every node (and all their relationships) from the Kuzu graph.

        Returns the number of nodes that were removed.
        """
        rows = self.execute_query("MATCH (n:Node) RETURN count(n) AS cnt", {})
        count = rows[0]["cnt"] if rows else 0
        self.execute_query("MATCH (n:Node) DETACH DELETE n", {})
        logger.info(f"[Graph] Wiped {count} nodes from the graph.")
        return count

    def clear_all_communities(self) -> list[str]:
        """Delete all community nodes and their MEMBER_OF / CONTAINS relationships."""
        existing = self.execute_query(
            "MATCH (c:Node) WHERE c.kind = 'community' RETURN c.id AS community_id",
            {},
        )
        community_ids = [
            row["community_id"] for row in existing if row.get("community_id")
        ]
        self.execute_query("MATCH ()-[r:MEMBER_OF]->() DELETE r", {})
        self.execute_query(
            "MATCH (c:Node) WHERE c.kind = 'community' DETACH DELETE c", {}
        )
        self._qdrant.delete_community_relationships()
        return community_ids

    def clear_all_temporal_digests(self) -> list[str]:
        """Delete all temporal_digest nodes from the graph."""
        existing = self.execute_query(
            "MATCH (d:Node) WHERE d.kind = 'temporal_digest' RETURN d.id AS digest_id",
            {},
        )
        digest_ids = [row["digest_id"] for row in existing if row.get("digest_id")]
        self.execute_query(
            "MATCH (d:Node) WHERE d.kind = 'temporal_digest' DETACH DELETE d", {}
        )
        return digest_ids

    def create_temporal_digest_node(
        self,
        node_id: str,
        name: str,
        summary: str,
        period_key: str,
    ) -> None:
        """Create or update a temporal digest node and store its embedding in Qdrant."""
        from app.services.embedding import embedding_service as _emb

        self.execute_query(
            """
            MERGE (d:Node {id: $node_id})
            ON CREATE SET d.kind = 'temporal_digest', d.type = 'temporal_digest', d.name = $name
            ON MATCH SET d.name = $name
            """,
            {"node_id": node_id, "name": name},
        )
        try:
            vector = _emb.embed_documents([summary])[0]
            self._qdrant.upsert_node_core(
                node_id=node_id,
                name=name,
                node_type="temporal_digest",
                description=summary,
                description_vector=vector,
                extra_payload={"period_key": period_key},
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"[Graph] create_temporal_digest_node Qdrant write failed for {node_id}: {exc}"
            )

    def set_node_community_membership(
        self, node_ids: list[str], community_id: str, community_level: int
    ) -> None:
        """Assign nodes to a Leiden community at the given hierarchy level.

        One UNWIND statement for the whole batch instead of a query per node;
        IDs that don't match an existing node simply produce no rows.
        """
        if not node_ids:
            return
        self.execute_query(
            """
            MATCH (c:Node {id: $community_id})
            UNWIND $node_ids AS node_id
            MATCH (n:Node {id: node_id})
            MERGE (n)-[r:MEMBER_OF]->(c)
            SET r.level = $community_level
            """,
            {
                "node_ids": node_ids,
                "community_id": community_id,
                "community_level": community_level,
            },
        )

    def create_leiden_community(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        community_id: str,
        community_level: int,
        name: str,
        summary: str,
        member_node_ids: list[str],
    ) -> dict:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Create or update a Leiden community node and link it to its member nodes."""
        from app.services.embedding import embedding_service as _emb

        self.execute_query(
            """
            MERGE (c:Node {id: $community_id})
            ON CREATE SET c.kind = 'community', c.type = 'community', c.name = $name
            ON MATCH SET c.name = $name
            """,
            {"community_id": community_id, "name": name},
        )

        if member_node_ids:
            try:
                self.execute_query(
                    """
                    MATCH (c:Node {id: $community_id})
                    UNWIND $member_ids AS member_id
                    MATCH (n:Node {id: member_id})
                    MERGE (c)-[:CONTAINS]->(n)
                    """,
                    {"community_id": community_id, "member_ids": member_node_ids},
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(
                    f"[Graph] CONTAINS edges for {community_id} skipped: {exc}"
                )

        try:
            summary_text = summary or name or community_id
            summary_vector = _emb.embed_documents([summary_text])[0]
            self._qdrant.upsert_node_core(
                node_id=community_id,
                name=name,
                node_type="community",
                description=summary_text,
                description_vector=summary_vector,
                community_level=community_level,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"[Graph] create_leiden_community Qdrant write failed: {exc}"
            )

        return {"community_id": community_id}

    def get_node_storage_payload(self, node_id: str) -> dict | None:
        """Return the full storage payload (name, type, contexts, facts) for a node."""
        rows = self.execute_query(
            "MATCH (n:Node {id: $node_id}) RETURN n.id AS node_id, [n.kind] AS labels",
            {"node_id": node_id},
        )
        if not rows:
            return None
        row = dict(rows[0])

        try:
            content = self._qdrant.get_node_content_by_id(node_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"[Graph] get_node_storage_payload Qdrant fetch failed for {node_id}: {exc}"
            )
            content = None

        try:
            rels = self._qdrant.get_relationships_for_node_ids([node_id])
            relationship_natural_language = [
                r["natural_language"] for r in rels if r.get("natural_language")
            ]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                f"[Graph] get_node_storage_payload NL fetch failed for {node_id}: {exc}"
            )
            relationship_natural_language = []

        row["name"] = (content or {}).get("name", "")
        row["description"] = _strip_facts_prefix((content or {}).get("description", ""))
        row["facts"] = (content or {}).get("facts", [])
        row["potential_questions"] = (content or {}).get("potential_questions", [])
        row["isolated_contexts"] = (content or {}).get("isolated_contexts", [])
        row["community_level"] = (content or {}).get("community_level")
        row["relationship_natural_language"] = relationship_natural_language
        return row

    def get_linked_evidence(
        self, node_names: list[str], limit_per_node: int = 3
    ) -> list[dict]:
        """Return isolated-context evidence sentences linked to the given source note."""
        if not node_names:
            return []
        normalized_names = [n.lower().strip() for n in node_names if n and n.strip()]
        if not normalized_names:
            return []

        name_to_id = self._qdrant.find_node_ids_by_names(normalized_names)
        id_to_name = {nid: name for name, nid in name_to_id.items() if nid and name}
        if not id_to_name:
            return []

        return self.get_linked_evidence_by_node_ids(
            list(id_to_name.keys()),
            limit_per_node=limit_per_node,
            node_id_to_name=id_to_name,
        )

    def get_linked_evidence_by_node_ids(
        self,
        node_ids: list[str],
        limit_per_node: int = 3,
        node_id_to_name: dict[str, str] | None = None,
    ) -> list[dict]:
        """Fetch linked note evidence for known node IDs.

        This avoids brittle name-to-id resolution when candidate names are aliases,
        enriched variants, or missing from sub-collection payloads.
        """
        if not node_ids:
            return []

        unique_node_ids = list(dict.fromkeys(nid for nid in node_ids if nid))
        if not unique_node_ids:
            return []

        query = """
        UNWIND $node_ids AS node_id
        MATCH (n:Node {id: node_id})<-[r:REFERENCES]-(note:Node)
        WHERE note.kind = 'note'
        WITH node_id, collect(DISTINCT {id: note.id, title: note.name}) AS evidence
        RETURN node_id, evidence
        """
        rows = self.execute_query(query, {"node_ids": unique_node_ids})
        id_to_name = {
            nid: (name or "").lower().strip()
            for nid, name in (node_id_to_name or {}).items()
            if nid
        }
        for row in rows:
            row["evidence"] = (row.get("evidence") or [])[:limit_per_node]
            row["node_name"] = id_to_name.get(row.get("node_id", ""), "")
        return rows

    # ---- Relationship management -------------------------------------------

    def create_or_update_relationship(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements,unused-argument
        self,
        source_name: str,
        source_label: str,
        target_name: str,
        target_label: str,
        relationship_type: str,
        natural_language: str = "",
        relationship_id: str = None,
        note_id: str = None,
        source_id: str = "",
        target_id: str = "",
    ) -> dict:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Upsert a SEMANTIC_REL edge between two nodes."""
        import uuid as _uuid
        from datetime import datetime

        if not relationship_type or not relationship_type.strip():
            raise ValueError(
                f"relationship_type cannot be empty for {source_name} -> {target_name}"
            )

        relationship_type = re.sub(r"[^A-Za-z0-9_]", "_", relationship_type.strip())

        if not relationship_id:
            relationship_id = str(_uuid.uuid4())

        if not source_id and source_name:
            source_id = self.resolve_node_id(source_name.lower().strip()) or ""
        if not target_id and target_name:
            target_id = self.resolve_node_id(target_name.lower().strip()) or ""

        if not source_id or not target_id:
            logger.warning(
                f"[Graph] Skipping relationship {source_name}->{target_name}: "
                f"unresolvable IDs (src={source_id!r} tgt={target_id!r})"
            )
            return {
                "action": "failed",
                "reason": "unresolvable IDs",
                "source": source_name,
                "target": target_name,
                "relationship_type": relationship_type,
                "natural_language": natural_language,
                "relationship_id": relationship_id,
                "previous_type": None,
            }

        ingestion_time = datetime.utcnow().isoformat()

        existing = self.execute_query(
            """
            MATCH (source:Node {id: $source_id})-[r:SEMANTIC_REL]->(target:Node {id: $target_id})
            WHERE r.rel_type = $rel_type
            RETURN r.rel_type AS current_type
            LIMIT 1
            """,
            {
                "source_id": source_id,
                "target_id": target_id,
                "rel_type": relationship_type,
            },
        )

        action = "created"

        if existing:
            action = "reinforced"
            self.execute_query(
                """
                MATCH (source:Node {id: $source_id})-[r:SEMANTIC_REL]->(target:Node {id: $target_id})
                WHERE r.rel_type = $rel_type
                SET r.last_updated = $ingestion_time,
                    r.mention_count = coalesce(r.mention_count, 0) + 1,
                    r.relationship_id = CASE
                        WHEN r.relationship_id IS NULL THEN $relationship_id
                        ELSE r.relationship_id
                    END
                """,
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "rel_type": relationship_type,
                    "ingestion_time": ingestion_time,
                    "relationship_id": relationship_id,
                },
            )
        else:
            self.execute_query(
                "MERGE (n:Node {id: $id}) ON CREATE SET n.kind = 'indexable'",
                {"id": source_id},
            )
            self.execute_query(
                "MERGE (n:Node {id: $id}) ON CREATE SET n.kind = 'indexable'",
                {"id": target_id},
            )
            self.execute_query(
                """
                MATCH (source:Node {id: $source_id})
                MATCH (target:Node {id: $target_id})
                CREATE (source)-[r:SEMANTIC_REL]->(target)
                SET r.rel_type = $rel_type,
                    r.relationship_id = $relationship_id,
                    r.ingested_at = $ingestion_time,
                    r.last_updated = $ingestion_time,
                    r.mention_count = 1,
                    r.note_id = $note_id
                """,
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "rel_type": relationship_type,
                    "relationship_id": relationship_id,
                    "ingestion_time": ingestion_time,
                    "note_id": note_id,
                },
            )

        logger.info(
            f"[Graph] Relationship {action}: "
            f"({source_name})-[{relationship_type}]->({target_name})"
        )

        return {
            "action": action,
            "source": source_name,
            "target": target_name,
            "relationship_type": relationship_type,
            "natural_language": natural_language,
            "relationship_id": relationship_id,
        }

    def _hop_query(self, direction: str) -> str:
        """Return a Cypher query template for a 1-hop neighbour expansion.

        ``direction`` must be either ``"->"`` (outgoing) or ``"<-"`` (incoming).
        The arrow is inserted into the MATCH pattern so both directed queries
        can share a single definition.
        """
        if direction == "->":
            pattern = "(start:Node {id: $node_id})-[r:SEMANTIC_REL|REFERENCES]->(related:Node)"
        else:
            pattern = "(start:Node {id: $node_id})<-[r:SEMANTIC_REL|REFERENCES]-(related:Node)"
        return f"""
            MATCH {pattern}
            RETURN DISTINCT
                related.id AS node_id,
                related.name AS name,
                related.kind AS label,
                1 AS depth,
                [CASE WHEN label(r) = 'SEMANTIC_REL' THEN r.rel_type ELSE label(r) END] AS relationship_path,
                [coalesce(r.confidence, 1.0)] AS confidence_path,
                [NULL] AS context_path,
                [NULL] AS natural_language_path
            ORDER BY name
            """

    def get_related_nodes(
        self,
        node_name: str,
        max_depth: int = 2,
        node_id: str | None = None,
    ) -> list[dict]:
        """Return neighbouring nodes reachable within max_depth hops of the given node.

        Pass ``node_id`` when the caller already knows it to skip the
        Qdrant name→id resolution round trip.
        """
        node_id = node_id or self.resolve_node_id(node_name.lower().strip())
        if not node_id:
            return []

        # Fast path for the common retrieval case (1-hop expansion).
        # This avoids variable-length path/list comprehension constructs that can
        # trigger parser assertions in some Kuzu builds.
        if max_depth == 1:
            # Run two directed queries so we can track edge direction correctly.
            # An undirected match loses directionality, causing the expansion prompt
            # to display incoming edges backwards (e.g. "corliss archer plays shirley
            # temple" instead of "shirley temple plays corliss archer").
            outgoing_query = self._hop_query("->")
            incoming_query = self._hop_query("<-")
            params = {"node_id": node_id}
            outgoing = self.execute_query(outgoing_query, params)
            for row in outgoing:
                row["edge_direction"] = "outgoing"
            incoming = self.execute_query(incoming_query, params)
            # Deduplicate: if a neighbor appears in both directions, keep outgoing.
            seen_ids: set[str] = {r["node_id"] for r in outgoing if r.get("node_id")}
            for row in incoming:
                row["edge_direction"] = "incoming"
                if row.get("node_id") not in seen_ids:
                    outgoing.append(row)
                    seen_ids.add(row["node_id"])
            return sorted(outgoing, key=lambda r: r.get("name") or "")

        # NOTE: Do NOT add `all(rel IN relationships(path) WHERE ...)` inside a
        # variable-length MATCH — it triggers a KU_UNREACHABLE parser assertion in
        # this Kuzu build.  Confidence filtering is done in Python
        # below after the query returns.
        query = f"""
        MATCH path = (start:Node {{id: $node_id}})-[:SEMANTIC_REL|REFERENCES*1..{max_depth}]-(related:Node)
        WITH related, relationships(path) AS rels, length(path) AS depth
        RETURN DISTINCT
            related.id AS node_id,
            related.name AS name,
            related.kind AS label,
            depth,
            [rel IN rels |
                CASE WHEN label(rel) = 'SEMANTIC_REL' THEN rel.rel_type
                     ELSE label(rel) END
            ] AS relationship_path,
            [rel IN rels | coalesce(rel.confidence, 0.0)] AS confidence_path,
            [rel IN rels | NULL] AS context_path,
            [rel IN rels | NULL] AS natural_language_path
        ORDER BY depth
        LIMIT 200
        """
        return self.execute_query(query, {"node_id": node_id})

    # ---- 3D spatial layout --------------------------------------------------

    def get_all_node_ids_and_edges(
        self,
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Return all node IDs and SEMANTIC_REL / MEMBER_OF edge pairs for layout computation."""
        node_rows = self.execute_query(
            """
            MATCH (n:Node)
            WHERE n.kind IN ['indexable', 'note', 'community']
              AND n.id IS NOT NULL
            RETURN n.id AS node_id
            """,
            {},
        )
        node_ids = [r["node_id"] for r in node_rows if r.get("node_id")]

        edge_rows = self.execute_query(
            """
            MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node)
            WHERE a.id IS NOT NULL AND b.id IS NOT NULL
            RETURN DISTINCT a.id AS src, b.id AS tgt
            UNION
            MATCH (n:Node)-[:MEMBER_OF]->(c:Node)
            WHERE n.id IS NOT NULL AND c.id IS NOT NULL
            RETURN DISTINCT n.id AS src, c.id AS tgt
            LIMIT 5000
            """,
            {},
        )
        edges = [
            (r["src"], r["tgt"]) for r in edge_rows if r.get("src") and r.get("tgt")
        ]
        return node_ids, edges

    def get_full_3d_graph(self) -> dict:  # pylint: disable=too-many-locals
        """Build the full 3-D graph payload (nodes + edges) for the canvas renderer."""
        indexable_rows = self.execute_query(
            """
            MATCH (n:Node)
            WHERE n.kind IN ['indexable', 'note']
              AND n.id IS NOT NULL AND n.name IS NOT NULL
            RETURN n.id AS node_id, n.name AS name, n.type AS node_type
            """,
            {},
        )
        community_rows = self.execute_query(
            """
            MATCH (c:Node)
            WHERE c.kind = 'community'
              AND c.id IS NOT NULL AND c.name IS NOT NULL
            RETURN c.id AS node_id, c.name AS name, NULL AS community_level
            """,
            {},
        )
        membership_rows = self.execute_query(
            """
            MATCH (n:Node)-[r:MEMBER_OF]->(c:Node)
            WHERE n.id IS NOT NULL AND c.id IS NOT NULL
            RETURN n.id AS node_id, c.id AS community_id, r.level AS level
            ORDER BY r.level DESC
            """,
            {},
        )

        node_community_map: dict[str, str] = {}
        node_level_map: dict[str, dict[int, str]] = {}
        community_level_map: dict[str, int] = {}
        for row in membership_rows:
            nid = row.get("node_id")
            cid = row.get("community_id")
            lvl = row.get("level")
            if nid and cid:
                if nid not in node_community_map:
                    node_community_map[nid] = cid
                if lvl is not None:
                    lvl_int = int(lvl)
                    node_level_map.setdefault(nid, {})[lvl_int] = cid
                    # Derive community level from the membership edge level —
                    # community_level is not stored on the community node itself.
                    community_level_map[cid] = lvl_int

        from app.utils.graph_layout import compute_solar_positions

        all_node_ids = [r["node_id"] for r in indexable_rows if r.get("node_id")]
        communities_meta = [
            {
                "community_id": r["node_id"],
                "community_level": community_level_map.get(r["node_id"], 0),
                "name": r.get("name", ""),
            }
            for r in community_rows
            if r.get("node_id")
        ]
        positions = compute_solar_positions(
            communities=communities_meta,
            node_level_map=node_level_map,
            all_node_ids=all_node_ids,
        )

        nodes = []
        for row in indexable_rows:
            nid = row.get("node_id")
            name = (row.get("name") or "").strip()
            if not nid or not name:
                continue
            x, y, z = positions.get(nid, (0.0, 0.0, 0.0))
            nodes.append(
                {
                    "node_id": nid,
                    "name": name,
                    "node_type": row.get("node_type") or "unknown",
                    "description": "",
                    "community_id": node_community_map.get(nid),
                    "x": float(x),
                    "y": float(y),
                    "z": float(z),
                }
            )
        for row in community_rows:
            nid = row.get("node_id")
            name = (row.get("name") or "").strip()
            if not nid or not name:
                continue
            x, y, z = positions.get(nid, (0.0, 0.0, 0.0))
            nodes.append(
                {
                    "node_id": nid,
                    "name": name,
                    "node_type": "community",
                    "description": "",
                    "community_id": None,
                    "x": float(x),
                    "y": float(y),
                    "z": float(z),
                }
            )

        edge_rows = self.execute_query(
            """
            MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node)
            WHERE a.id IS NOT NULL AND b.id IS NOT NULL
              AND a.kind IN ['indexable', 'note']
              AND b.kind IN ['indexable', 'note']
            RETURN DISTINCT a.id AS source, b.id AS target, r.rel_type AS rel_type
            LIMIT 4000
            """,
            {},
        )
        # Build MEMBER_OF edges from membership_rows (already fetched without a
        # limit for the layout calculation) so no edges are dropped by a cap.
        seen_member = set()
        member_edge_rows = []
        for row in membership_rows:
            nid = row.get("node_id")
            cid = row.get("community_id")
            if nid and cid:
                key = (nid, cid)
                if key not in seen_member:
                    seen_member.add(key)
                    member_edge_rows.append(
                        {"source": nid, "target": cid, "rel_type": "MEMBER_OF"}
                    )
        edges = [
            {
                "source": r["source"],
                "target": r["target"],
                "type": r.get("rel_type", ""),
            }
            for r in (list(edge_rows) + member_edge_rows)
            if r.get("source") and r.get("target")
        ]
        return {"nodes": nodes, "edges": edges}

    def store_node_positions(
        self, positions: dict[str, tuple[float, float, float]]
    ) -> None:
        """Persist pre-computed 3-D (x, y, z) positions for a batch of nodes.

        Writes are batched via UNWIND (one statement per chunk instead of one
        per node); if a batch fails we fall back to per-node writes for that
        chunk so a single bad row can't drop the whole layout.
        """
        if not positions:
            return
        rows = [
            {
                "node_id": nid,
                "x": float(xyz[0]),
                "y": float(xyz[1]),
                "z": float(xyz[2]),
            }
            for nid, xyz in positions.items()
        ]
        batch_query = """
            UNWIND $rows AS row
            MATCH (n:Node {id: row.node_id})
            SET n.pos_x = row.x, n.pos_y = row.y, n.pos_z = row.z
        """
        single_query = """
            MATCH (n:Node {id: $node_id})
            SET n.pos_x = $x, n.pos_y = $y, n.pos_z = $z
        """
        chunk_size = 500
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            try:
                self.execute_query(batch_query, {"rows": chunk})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(
                    f"[Graph] store_node_positions batch of {len(chunk)} failed "
                    f"({exc}); retrying per-node"
                )
                for row in chunk:
                    try:
                        self.execute_query(single_query, row)
                    except Exception as row_exc:  # pylint: disable=broad-exception-caught
                        logger.debug(
                            f"[Graph] store_node_positions skipped {row['node_id']}: {row_exc}"
                        )

    def get_node_connections(self, node_id: str, *, limit: int = 16) -> list[dict]:
        """1-hop neighbours for a node id (semantic + REFERENCES)."""
        if not node_id:
            return []
        params = {"node_id": node_id}
        outgoing = self.execute_query(self._hop_query("->"), params)
        for row in outgoing:
            row["edge_direction"] = "outgoing"
        incoming = self.execute_query(self._hop_query("<-"), params)
        seen: set[str] = {r["node_id"] for r in outgoing if r.get("node_id")}
        for row in incoming:
            row["edge_direction"] = "incoming"
            if row.get("node_id") not in seen:
                outgoing.append(row)
                seen.add(row["node_id"])
        rows = sorted(outgoing, key=lambda r: (r.get("name") or "").lower())
        return rows[: max(1, limit)]

    def get_notes_referencing_node(self, node_id: str, *, limit: int = 8) -> list[dict]:
        """Notes that REFERENCES this entity/concept node."""
        if not node_id:
            return []
        rows = self.execute_query(
            """
            MATCH (note:Node)-[r:REFERENCES]->(n:Node {id: $nid})
            WHERE note.kind = 'note'
            RETURN DISTINCT note.id AS note_id, note.name AS name,
                   coalesce(r.note_id, note.id) AS source_note_id
            LIMIT $lim
            """,
            {"nid": node_id, "lim": int(limit)},
        )
        return [
            {
                "note_id": r.get("note_id") or r.get("source_note_id"),
                "name": r.get("name") or "Untitled note",
            }
            for r in rows
            if r.get("note_id") or r.get("source_note_id")
        ]

    def get_node_detail(self, node_id: str) -> dict | None:
        """Return detailed content for a single node, including contexts, facts, and community membership."""
        rows = self.execute_query(
            """
            MATCH (n:Node {id: $nid})
            WHERE n.kind IN ['indexable', 'note']
            OPTIONAL MATCH (n)-[:MEMBER_OF]->(c:Node)
            RETURN n.id AS node_id, n.name AS name, n.type AS node_type,
                   c.id AS community_id, c.name AS community_name, n.kind AS kind
            LIMIT 1
            """,
            {"nid": node_id},
        )
        if not rows:
            rows = self.execute_query(
                """
                MATCH (c:Node {id: $nid})
                WHERE c.kind = 'community'
                RETURN c.id AS node_id, c.name AS name,
                       'community' AS node_type, NULL AS community_id,
                       NULL AS community_name, 'community' AS kind
                LIMIT 1
                """,
                {"nid": node_id},
            )
        if not rows:
            return None

        row = rows[0]
        nid = row.get("node_id")
        if not nid:
            return None

        content_map = self._qdrant.get_nodes_content_by_ids([nid])
        c = content_map.get(nid, {})
        # Single-id path can recover contexts even when bulk core retrieve misses.
        if not c.get("description") and not c.get("isolated_contexts"):
            single = self._qdrant.get_node_content_by_id(nid) or {}
            if single:
                c = {**c, **single}

        connections = self.get_node_connections(nid)
        related_notes = self.get_notes_referencing_node(nid)

        return {
            "node_id": nid,
            "name": c.get("name") or row.get("name") or "",
            "node_type": row.get("node_type") or "unknown",
            "description": _strip_facts_prefix(c.get("description") or ""),
            "isolated_contexts": c.get("isolated_contexts") or [],
            "facts": c.get("facts") or [],
            "domain": c.get("domain"),
            "status": c.get("status"),
            "community_id": row.get("community_id"),
            "community_name": row.get("community_name"),
            "summary": _strip_facts_prefix(c.get("description") or ""),
            "themes": c.get("themes") or [],
            "member_count": c.get("member_count") or 0,
            "connections": [
                {
                    "node_id": conn.get("node_id"),
                    "name": (conn.get("name") or "").strip(),
                    "kind": conn.get("label"),
                    "relationship": (
                        (conn.get("relationship_path") or [None])[0]
                        if isinstance(conn.get("relationship_path"), list)
                        else conn.get("relationship_path")
                    ),
                    "direction": conn.get("edge_direction"),
                }
                for conn in connections
                if conn.get("node_id")
            ],
            "related_notes": related_notes,
        }


graph_service = GraphService()
