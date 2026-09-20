"""Unit tests for app/schemas/extraction.py — Pydantic model validators.

Tests cover handle_none, the closed relationship vocabulary, and the
Extraction outer-wrapper normalizer. All tests are synchronous with no I/O.
"""


from app.schemas.extraction import RELATIONSHIP_TYPES, ExtractedRelationship, Extraction, Node

# ── Node.normalize_keys ───────────────────────────────────────────────────────


# ── Node.handle_none ──────────────────────────────────────────────────────────


class TestNodeHandleNone:
    def test_none_name_becomes_empty_string(self):
        node = Node.model_validate({"name": None, "type": "person"})
        assert node.name == ""

    def test_none_type_becomes_thing(self):
        node = Node.model_validate({"name": "X", "type": None})
        assert node.type == "thing"

    def test_none_isolated_context_becomes_empty_string(self):
        node = Node.model_validate({"name": "X", "isolated_context": None})
        assert node.isolated_context == ""

    def test_valid_type_preserved(self):
        node = Node.model_validate({"name": "Paris", "type": "city"})
        assert node.type == "city"


# ── ExtractedRelationship.relationship_type ──────────────────────────────────


class TestRelationshipTypeVocabulary:
    def _rel(self, rel_type):
        return ExtractedRelationship.model_validate(
            {"source_name": "A", "target_name": "B", "relationship_type": rel_type}
        ).relationship_type

    def test_none_and_empty_become_related_to(self):
        assert self._rel(None) == "related_to"
        assert self._rel("") == "related_to"

    def test_unknown_predicate_becomes_related_to(self):
        assert self._rel("plays_corliss_archer") == "related_to"
        assert self._rel("is_friends_with") == "related_to"  # no fuzzy matching

    def test_known_predicate_is_normalised_not_rejected(self):
        assert self._rel(" Lives In ") == "lives_in"
        assert self._rel("WORKS_AT") == "works_at"

    def test_every_listed_predicate_round_trips(self):
        for t in RELATIONSHIP_TYPES:
            assert self._rel(t) == t


# ── Extraction.normalize_keys ─────────────────────────────────────────────────


class TestExtractionNormalizeKeys:
    def test_bare_node_list_wraps_into_extraction(self):
        raw = [{"name": "Alice", "type": "person"}, {"name": "Paris", "type": "city"}]
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 2
        assert ext.relationships == []

    def test_gemma3_two_list_format(self):
        raw = [
            [{"name": "Alice", "type": "person"}],
            [
                {
                    "source_name": "Alice",
                    "target_name": "Bob",
                    "relationship_type": "knows",
                }
            ],
        ]
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 1
        assert len(ext.relationships) == 1

    def test_extraction_wrapper_unwrapped(self):
        raw = {
            "extraction": {
                "nodes": [{"name": "Alice", "type": "person"}],
                "relationships": [],
            }
        }
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 1

    def test_data_wrapper_unwrapped(self):
        raw = {
            "data": {
                "nodes": [{"name": "Bob"}],
                "relationships": [],
            }
        }
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 1

    def test_result_wrapper_unwrapped(self):
        raw = {
            "result": {
                "nodes": [{"name": "Charlie"}],
                "relationships": [],
            }
        }
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 1

    def test_string_items_in_bare_list_become_nodes(self):
        raw = ["Alice", "Bob", "Paris"]
        ext = Extraction.model_validate(raw)
        names = {n.name for n in ext.nodes}
        assert "Alice" in names
        assert "Bob" in names
        assert "Paris" in names

    def test_embedded_relationships_hoisted(self):
        raw = [
            {
                "name": "Alice",
                "type": "person",
                "relationships": [
                    {
                        "source_name": "Alice",
                        "target_name": "Bob",
                        "relationship_type": "knows",
                    }
                ],
            }
        ]
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 1
        assert len(ext.relationships) == 1

    def test_normal_dict_with_nodes_and_relationships(self):
        raw = {
            "nodes": [{"name": "Alice"}, {"name": "Bob"}],
            "relationships": [
                {
                    "source_name": "Alice",
                    "target_name": "Bob",
                    "relationship_type": "knows",
                }
            ],
        }
        ext = Extraction.model_validate(raw)
        assert len(ext.nodes) == 2
        assert len(ext.relationships) == 1
