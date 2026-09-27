"""Pydantic schemas for LLM-extracted knowledge graph nodes and relationships."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(min_length=1)]


class Node(BaseModel):
    """One entity. ``type`` is free text (person, song, event); every field is required."""

    name: Name
    type: Name
    isolated_context: Name


class ExtractedRelationship(BaseModel):
    """Relationship between two entities, named exactly as the entities are."""

    source_name: Name
    target_name: Name
    # The model's own predicate, as written: no vocabulary, no normalisation.
    relationship_type: Name
    natural_language: Name


def _require_known_endpoints(relationships: list[ExtractedRelationship], names: set[str]) -> None:
    for rel in relationships:
        for end in (rel.source_name, rel.target_name):
            if end not in names:
                raise ValueError(f"relationship names {end!r}, which is not one of the entities")


class Extraction(BaseModel):
    """Root extraction result — nodes and relationships found in a note."""

    nodes: list[Node]
    relationships: list[ExtractedRelationship]
    title: str | None = None

    @model_validator(mode="after")
    def endpoints_are_entities(self) -> "Extraction":
        _require_known_endpoints(self.relationships, {n.name for n in self.nodes})
        return self


class NoteInput(BaseModel):
    """Create or re-ingest a note (legacy ``POST /ingest`` + pipeline)."""

    content: str
    created_at: str | None = None
    title: str | None = None  # If provided, use instead of auto-generating
    skip_ingestion: bool = False  # Save metadata/vault only; skip graph ingest


class EntityName(BaseModel):
    """An entity as pass 1 lists it: what exists, not yet described."""

    name: Name
    type: Name


class EntityPass(BaseModel):
    """Pass 1 of task-split extraction: what exists, over the whole note.

    Names and types only. The output is small enough that a long note fits in
    one call, which is the point — every later pass then works from one
    complete, canonical entity list instead of rediscovering entities per
    chunk and hoping the names match.
    """

    title: str | None = None
    nodes: list[EntityName]


class RelationshipPass(BaseModel):
    """Pass 2: how the entities connect, with the full entity list in context.

    Seeing the whole note and every entity at once is what makes a relationship
    across two distant sections expressible at all — under chunking neither
    side could state it.
    """

    relationships: list[ExtractedRelationship]


class NodeContext(BaseModel):
    """One entity's description, from pass 3."""

    name: Name
    isolated_context: Name


class ContextPass(BaseModel):
    """Pass 3: descriptions for known entities.

    This is the pass that may still be chunked, and safely so — a description
    is drawn from where the entity appears, and the entity list is already
    fixed, so chunking here cannot invent or split an entity.
    """

    contexts: list[NodeContext]
