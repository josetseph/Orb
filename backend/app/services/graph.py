"""Graph service backed by Kuzu (embedded graph database).

Schema
------
Node table:
    Node(id STRING PRIMARY KEY, kind STRING, name STRING, type STRING)

  kind values: 'note' | 'indexable' | 'community'

Relationship tables:
    REFERENCES(FROM Node TO Node, note_id STRING)
    MEMBER_OF(FROM Node TO Node, level INT64)
    CONTAINS(FROM Node TO Node)
    Alias(name STRING PRIMARY KEY, node_id STRING)
    SEMANTIC_REL(FROM Node TO Node,
        rel_type STRING, confidence DOUBLE, strength DOUBLE,
        relevance DOUBLE, edge_weight DOUBLE, relationship_id STRING,
        ingested_at STRING, last_updated STRING, mention_count INT64,
        note_id STRING, note_ids STRING[])

  ``note_ids`` are the notes asserting the edge (``note_id`` the first of them),
  so a note re-ingested or resumed takes back exactly what it said.

  ``Alias`` maps a name merged away to the entity it now means (entity_merge.py).
  A row whose node has since gone is simply never returned.

  ``pos_x/pos_y/pos_z``, ``is_similarity`` and ``created_at`` may still exist
  in databases created before they were dropped; nothing reads or writes them.
  Layout is computed per request by ``get_full_3d_graph``.

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


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_STMTS = [
    """CREATE NODE TABLE IF NOT EXISTS Node(
        id STRING,
        kind STRING,
        name STRING,
        type STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Alias(
        name STRING,
        node_id STRING,
        PRIMARY KEY(name)
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
        note_ids STRING[]
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

        self.db = kuzu.Database(str(_db_path))
        self.conn = kuzu.Connection(self.db)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        """Create all tables if they do not exist."""
        for stmt in _SCHEMA_STMTS:
            try:
                with self._lock:
                    self.conn.execute(stmt)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if "already exist" not in str(exc).lower():
                    logger.warning(f"[Graph] Schema init: {exc}")
        try:
            with self._lock:
                self.conn.execute("ALTER TABLE SEMANTIC_REL ADD note_ids STRING[]")
        except Exception:  # pylint: disable=broad-exception-caught
            return  # already there
        # Edges from before note_ids: every note referencing both ends may have
        # asserted it, plus the one recorded. Too many keeps an edge a little
        # longer; too few would drop one a note still asserts.
        with self._lock:
            self.conn.execute(
                """
                MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node)
                OPTIONAL MATCH (n:Node)-[:REFERENCES]->(a), (n)-[:REFERENCES]->(b)
                WITH r, collect(DISTINCT n.id) AS ids
                SET r.note_ids = CASE
                    WHEN r.note_id IS NULL OR list_contains(ids, r.note_id) THEN ids
                    ELSE list_append(ids, r.note_id)
                END
                """
            )
            self.conn.execute("MATCH ()-[r:SEMANTIC_REL]->() SET r.mention_count = size(r.note_ids)")
        logger.info("[Graph] Edges now record every note asserting them")

    def close(self) -> None:
        """Close the Kuzu database connection."""
        try:
            self.conn.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def resolve_node_id(self, name: str) -> str | None:
        """Resolve a node name to its stable node_id via Qdrant node_cores."""
        found = self._qdrant.find_node_id_by_name(name)
        if found:
            return found
        alias = self.resolve_aliases([name]).get(name.lower().strip())
        return alias["id"] if alias else None

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
        for nm, alias in self.resolve_aliases(normalized).items():
            name_to_id.setdefault(nm, alias["id"])
        return name_to_id

    # ---- Aliases and merging (entity_merge.py) ------------------------------

    def resolve_aliases(self, names: list[str]) -> dict[str, dict]:
        """For each name that was merged away: ``{name: {"id", "name"}}`` of the entity it means."""
        if not names:
            return {}
        rows = self.execute_query(
            "UNWIND $names AS q MATCH (a:Alias {name: q}) MATCH (n:Node {id: a.node_id}) "
            "WHERE n.kind = 'indexable' RETURN a.name AS alias, n.id AS id, n.name AS name",
            {"names": list(names)},
        )
        return {r["alias"]: {"id": r["id"], "name": r["name"]} for r in rows}

    def aliases_of(self, node_id: str) -> list[str]:
        rows = self.execute_query(
            "MATCH (a:Alias) WHERE a.node_id = $id RETURN a.name AS name ORDER BY name", {"id": node_id}
        )
        return [r["name"] for r in rows]

    def set_aliases(self, names: list[str], node_id: str) -> None:
        if names:
            self.execute_query(
                "UNWIND $names AS x MERGE (a:Alias {name: x}) SET a.node_id = $id",
                {"names": names, "id": node_id},
            )

    def drop_aliases(self, names: list[str]) -> None:
        if names:
            self.execute_query("UNWIND $names AS x MATCH (a:Alias {name: x}) DELETE a", {"names": names})

    def get_entity(self, node_id: str) -> dict | None:
        rows = self.execute_query(
            "MATCH (n:Node {id: $id}) RETURN n.id AS id, n.kind AS kind, n.name AS name, n.type AS type",
            {"id": node_id},
        )
        return rows[0] if rows else None

    def restore_entity(self, entity: dict) -> None:
        self.execute_query(
            "MERGE (n:Node {id: $id}) SET n.kind = $kind, n.name = $name, n.type = $type",
            {k: entity[k] for k in ("id", "kind", "name", "type")},
        )

    def delete_entity(self, node_id: str) -> None:
        self.execute_query("MATCH (n:Node {id: $id}) DETACH DELETE n", {"id": node_id})

    def note_references(self, node_id: str) -> list[str]:
        rows = self.execute_query(
            "MATCH (n:Node)-[:REFERENCES]->(:Node {id: $id}) RETURN DISTINCT n.id AS id", {"id": node_id}
        )
        return [r["id"] for r in rows]

    def add_references(self, note_ids: list[str], node_id: str) -> None:
        if note_ids:
            self.execute_query(
                "UNWIND $notes AS nid MATCH (note:Node {id: nid}), (e:Node {id: $id}) "
                "MERGE (note)-[r:REFERENCES]->(e) SET r.note_id = nid",
                {"notes": note_ids, "id": node_id},
            )

    def drop_references(self, note_ids: list[str], node_id: str) -> None:
        if note_ids:
            self.execute_query(
                "UNWIND $notes AS nid MATCH (:Node {id: nid})-[r:REFERENCES]->(:Node {id: $id}) DELETE r",
                {"notes": note_ids, "id": node_id},
            )

    def entity_edges(self, node_id: str) -> list[dict]:
        """Every SEMANTIC_REL touching the node, with all its properties."""
        return self.execute_query(
            """
            MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node) WHERE a.id = $id OR b.id = $id
            RETURN a.id AS source, b.id AS target, r.rel_type AS rel_type,
                   r.relationship_id AS relationship_id, r.confidence AS confidence,
                   r.strength AS strength, r.relevance AS relevance, r.edge_weight AS edge_weight,
                   r.ingested_at AS ingested_at, r.last_updated AS last_updated,
                   r.note_id AS note_id, r.note_ids AS note_ids
            """,
            {"id": node_id},
        )

    def _edge_note_ids(self, source: str, target: str, rel_type: str) -> list[str] | None:
        rows = self.execute_query(
            "MATCH (:Node {id: $s})-[r:SEMANTIC_REL]->(:Node {id: $t}) WHERE r.rel_type = $rt "
            "RETURN r.note_ids AS ids LIMIT 1",
            {"s": source, "t": target, "rt": rel_type},
        )
        return (rows[0]["ids"] or []) if rows else None

    def _set_edge_notes(self, source: str, target: str, rel_type: str, ids: list[str]) -> None:
        value = "$ids" if ids else "CAST([] AS STRING[])"  # Kuzu types an empty parameter INT64[]
        params = {"s": source, "t": target, "rt": rel_type, "n": len(ids)}
        if ids:
            params["ids"] = ids
        self.execute_query(
            "MATCH (:Node {id: $s})-[r:SEMANTIC_REL]->(:Node {id: $t}) WHERE r.rel_type = $rt "
            f"SET r.note_ids = {value}, r.mention_count = $n",
            params,
        )

    def absorb_edge(self, source: str, target: str, edge: dict) -> None:
        """Give (source)-[edge's type]->(target) the edge's notes, creating it if absent. Repeatable."""
        rel_type, notes = edge["rel_type"], edge.get("note_ids") or []
        live = self._edge_note_ids(source, target, rel_type)
        if live is not None:
            self._set_edge_notes(source, target, rel_type, live + [n for n in notes if n not in live])
            return
        self.execute_query(
            """
            MATCH (s:Node {id: $s}), (t:Node {id: $t})
            CREATE (s)-[r:SEMANTIC_REL]->(t)
            SET r.rel_type = $rt, r.relationship_id = $rid, r.confidence = $confidence,
                r.strength = $strength, r.relevance = $relevance, r.edge_weight = $edge_weight,
                r.ingested_at = $ingested_at, r.last_updated = $last_updated, r.note_id = $note_id
            """,
            {
                "s": source, "t": target, "rt": rel_type, "rid": edge.get("relationship_id"),
                **{k: edge.get(k) for k in (
                    "confidence", "strength", "relevance", "edge_weight",
                    "ingested_at", "last_updated", "note_id",
                )},
            },
        )
        self._set_edge_notes(source, target, rel_type, notes)

    def retract_edge(self, source: str, target: str, rel_type: str, notes: list[str], created: bool) -> None:
        """Undo ``absorb_edge``: take ``notes`` back; an edge it ``created`` goes once none are left."""
        live = self._edge_note_ids(source, target, rel_type)
        if live is None:
            return
        left = [n for n in live if n not in notes]
        if created and not left:
            self.execute_query(
                "MATCH (:Node {id: $s})-[r:SEMANTIC_REL]->(:Node {id: $t}) WHERE r.rel_type = $rt DELETE r",
                {"s": source, "t": target, "rt": rel_type},
            )
        else:
            self._set_edge_notes(source, target, rel_type, left)

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
        """Return the full storage payload (name, type, contexts) for a node."""
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
        row["description"] = (content or {}).get("description", "")
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
                    r.note_ids = CASE
                        WHEN $note_id IS NULL OR list_contains(r.note_ids, $note_id) THEN r.note_ids
                        ELSE list_append(coalesce(r.note_ids, CAST([] AS STRING[])), $note_id)
                    END,
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
                    "note_id": note_id,
                },
            )
            # Asserting it again (a re-ingest, a resumed write) does not count twice.
            self.execute_query(
                """
                MATCH (source:Node {id: $source_id})-[r:SEMANTIC_REL]->(target:Node {id: $target_id})
                WHERE r.rel_type = $rel_type
                SET r.mention_count = size(r.note_ids)
                """,
                {"source_id": source_id, "target_id": target_id, "rel_type": relationship_type},
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
                    r.mention_count = CASE WHEN $note_id IS NULL THEN 0 ELSE 1 END,
                    r.note_id = $note_id,
                    r.note_ids = CASE WHEN $note_id IS NULL THEN CAST([] AS STRING[]) ELSE [$note_id] END
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

    # ---- Re-ingest cleanup ---------------------------------------------------

    def clear_note_contribution(self, note_id: str) -> dict:
        """Take back what one note asserted, so re-ingesting it starts clean.

        Drops the note's REFERENCES edges and un-counts its SEMANTIC_REL
        assertions, deleting edges no other note restated. Returns the ids of
        the entities it referenced (``entity_ids`` — sweep them with
        ``delete_orphan_entities`` once the new extraction is written) and of
        the deleted edges (``relationship_ids`` — their Qdrant points go too).
        """
        params = {"note_id": note_id}
        entity_ids = [
            r["id"]
            for r in self.execute_query(
                "MATCH (:Node {id: $note_id})-[:REFERENCES]->(e:Node) "
                "RETURN DISTINCT e.id AS id",
                params,
            )
            if r.get("id")
        ]
        self.execute_query(
            "MATCH (:Node {id: $note_id})-[r:REFERENCES]->(:Node) DELETE r", params
        )
        gone = "WHERE list_contains(r.note_ids, $note_id) AND size(r.note_ids) <= 1"
        relationship_ids = [
            r["rid"]
            for r in self.execute_query(
                f"MATCH (:Node)-[r:SEMANTIC_REL]->(:Node) {gone} "
                "RETURN r.relationship_id AS rid",
                params,
            )
            if r.get("rid")
        ]
        self.execute_query(
            f"MATCH (:Node)-[r:SEMANTIC_REL]->(:Node) {gone} DELETE r", params
        )
        self.execute_query(
            "MATCH (:Node)-[r:SEMANTIC_REL]->(:Node) WHERE list_contains(r.note_ids, $note_id) "
            "SET r.mention_count = size(r.note_ids) - 1",
            params,
        )
        # Kuzu takes no parameter inside a lambda: bind it first.
        self.execute_query(
            "WITH $note_id AS nid MATCH (:Node)-[r:SEMANTIC_REL]->(:Node) "
            "WHERE list_contains(r.note_ids, nid) "
            "SET r.note_ids = list_filter(r.note_ids, x -> x <> nid)",
            params,
        )
        return {"entity_ids": entity_ids, "relationship_ids": relationship_ids}

    def delete_orphan_entities(self, candidate_ids: list[str]) -> list[str]:
        """DETACH DELETE the candidates no note references and no edge touches.

        Returns the ids removed so the caller can drop their Qdrant/Meili docs.
        """
        if not candidate_ids:
            return []
        rows = self.execute_query(
            """
            UNWIND $ids AS cid
            MATCH (n:Node {id: cid})
            WHERE n.kind = 'indexable'
              AND NOT EXISTS { MATCH (:Node)-[:REFERENCES]->(n) }
              AND NOT EXISTS { MATCH (n)-[:SEMANTIC_REL]-(:Node) }
            RETURN n.id AS id
            """,
            {"ids": candidate_ids},
        )
        orphans = [r["id"] for r in rows if r.get("id")]
        if orphans:
            self.execute_query(
                "UNWIND $ids AS oid MATCH (n:Node {id: oid}) DETACH DELETE n",
                {"ids": orphans},
            )
        return orphans

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
        """Return detailed content for a single node, including contexts and community membership."""
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
            "description": c.get("description") or "",
            "isolated_contexts": c.get("isolated_contexts") or [],
            "community_id": row.get("community_id"),
            "community_name": row.get("community_name"),
            "summary": c.get("description") or "",
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
