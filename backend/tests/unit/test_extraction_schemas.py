"""Unit tests for app/schemas/extraction.py — Pydantic model validators.

Tests cover normalize_keys, handle_none, and the complex Extraction
outer-wrapper normalizer. All tests are synchronous with no I/O.
"""


from app.schemas.extraction import ExtractedRelationship, Extraction, Node

# ── Node.normalize_keys ───────────────────────────────────────────────────────


class TestNodeNormalizeKeys:
    def test_trait_aliased_to_name(self):
        node = Node.model_validate({"trait": "bravery", "type": "quality"})
        assert node.name == "bravery"

    def test_title_aliased_to_name(self):
        node = Node.model_validate({"title": "Dr.", "type": "honorific"})
        assert node.name == "Dr."

    def test_name_takes_priority_over_trait(self):
        node = Node.model_validate(
            {"name": "Alice", "trait": "ignored", "type": "person"}
        )
        assert node.name == "Alice"

    def test_name_takes_priority_over_title(self):
        node = Node.model_validate(
            {"name": "Alice", "title": "ignored", "type": "person"}
        )
        assert node.name == "Alice"

    def test_evidence_quote_aliased_to_isolated_context(self):
        node = Node.model_validate({"name": "X", "evidence_quote": "some text"})
        assert node.isolated_context == "some text"

    def test_context_aliased_to_isolated_context(self):
        node = Node.model_validate({"name": "X", "context": "ctx"})
        assert node.isolated_context == "ctx"

    def test_isolated_context_takes_priority_over_context(self):
        node = Node.model_validate(
            {"name": "X", "isolated_context": "primary", "context": "secondary"}
        )
        assert node.isolated_context == "primary"


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


# ── ExtractedRelationship.normalize_keys ──────────────────────────────────────


class TestExtractedRelationshipNormalizeKeys:
    def test_entity1_aliased_to_source_name(self):
        rel = ExtractedRelationship.model_validate(
            {"entity1": "Alice", "entity2": "Bob", "relationship_type": "knows"}
        )
        assert rel.source_name == "Alice"

    def test_entity2_aliased_to_target_name(self):
        rel = ExtractedRelationship.model_validate(
            {"entity1": "Alice", "entity2": "Bob", "relationship_type": "knows"}
        )
        assert rel.target_name == "Bob"

    def test_description_aliased_to_natural_language(self):
        rel = ExtractedRelationship.model_validate(
            {
                "source_name": "Alice",
                "target_name": "Bob",
                "description": "Alice loves Bob",
                "relationship_type": "loves",
            }
        )
        assert rel.natural_language == "Alice loves Bob"

    def test_none_relationship_type_becomes_relates_to(self):
        rel = ExtractedRelationship.model_validate(
            {
                "source_name": "A",
                "target_name": "B",
                "relationship_type": None,
            }
        )
        assert rel.relationship_type == "relates_to"

    def test_empty_relationship_type_becomes_relates_to(self):
        rel = ExtractedRelationship.model_validate(
            {
                "source_name": "A",
                "target_name": "B",
                "relationship_type": "",
            }
        )
        assert rel.relationship_type == "relates_to"


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
