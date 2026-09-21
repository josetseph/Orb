"""The extraction schema accepts one shape: the one the prompt asks for.

It used to absorb whatever local models produced (bare lists, wrapper keys, nulls,
off-list predicates). A reply is now used as written or rejected and counted, so
every shape below that is not the specified one must fail validation.
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.extraction import RELATIONSHIP_TYPES, ExtractedRelationship, Extraction, Node

NODES = [
    {"name": "Alice", "type": "Person", "isolated_context": "Alice is an engineer."},
    {"name": "Bob", "type": "Person", "isolated_context": "Bob manages Alice."},
]
REL = {"source_name": "Bob", "target_name": "Alice", "relationship_type": "manages", "natural_language": "Bob manages Alice"}


def _parse(payload) -> Extraction:
    return Extraction.model_validate_json(json.dumps(payload))


def test_the_specified_shape_is_accepted_unchanged():
    got = _parse({"title": "Team", "nodes": NODES, "relationships": [REL]})
    assert [n.name for n in got.nodes] == ["Alice", "Bob"]
    assert got.relationships[0].relationship_type == "manages"
    assert got.title == "Team"


def test_title_is_the_only_optional_key():
    assert _parse({"nodes": NODES, "relationships": []}).title is None


@pytest.mark.parametrize("predicate", RELATIONSHIP_TYPES)
def test_every_listed_predicate_is_accepted(predicate):
    assert ExtractedRelationship(**{**REL, "relationship_type": predicate}).relationship_type == predicate


@pytest.mark.parametrize(
    "predicate",
    ["supervises", "Manages", "manages ", "works at", "", None],
    ids=["off-list", "wrong-case", "trailing-space", "spaced", "empty", "null"],
)
def test_a_predicate_is_never_rewritten(predicate):
    with pytest.raises(ValidationError):
        ExtractedRelationship(**{**REL, "relationship_type": predicate})


@pytest.mark.parametrize(
    "payload",
    [
        NODES,
        [NODES, [REL]],
        {"extraction": {"nodes": NODES, "relationships": []}},
        {"data": {"nodes": NODES, "relationships": []}},
        {"result": {"nodes": NODES, "relationships": []}},
        {"nodes": ["Alice", "Bob"], "relationships": []},
        {"nodes": NODES},
        {"relationships": []},
        {"nodes": None, "relationships": None},
        None,
    ],
    ids=["bare-list", "two-lists", "extraction-wrapper", "data-wrapper", "result-wrapper",
         "string-nodes", "no-relationships-key", "no-nodes-key", "null-lists", "null"],
)
def test_no_other_shape_is_absorbed(payload):
    with pytest.raises(ValidationError):
        _parse(payload)


@pytest.mark.parametrize("field", ["name", "type", "isolated_context"])
@pytest.mark.parametrize("value", [None, ""], ids=["null", "empty"])
def test_a_node_field_is_never_defaulted(field, value):
    with pytest.raises(ValidationError):
        Node(**{**NODES[0], field: value})


@pytest.mark.parametrize("end", ["source_name", "target_name"])
@pytest.mark.parametrize("name", ["Carol", "alice", "Alice ", "Alice (Person)"], ids=["unlisted", "case", "space", "type-echo"])
def test_a_relationship_must_name_its_entities_exactly(end, name):
    with pytest.raises(ValidationError, match="not one of the entities"):
        _parse({"nodes": NODES, "relationships": [{**REL, end: name}]})


def test_malformed_json_is_not_repaired():
    for raw in ('```json\n{"nodes": [], "relationships": []}\n```', '{"nodes": [], "relationships": [],}', "{'nodes': [], 'relationships': []}", ""):
        with pytest.raises(ValidationError):
            Extraction.model_validate_json(raw)
