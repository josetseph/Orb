"""Pydantic schemas for LLM-extracted knowledge graph nodes and relationships."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

class Node(BaseModel):
    """Single uniform node — LLM sets ``type`` freely (e.g. person, song, event)."""

    name: str = ""
    type: str = "thing"
    isolated_context: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def handle_none(cls, v: Any, info) -> Any:
        """Coerce None to safe defaults for each field."""
        if v is None:
            if info.field_name == "type":
                return "thing"
            return ""
        return v


#: The only predicates the graph stores. The model is shown this list; anything
#: else it returns collapses to ``related_to`` rather than minting a new edge
#: label per note.
RELATIONSHIP_TYPES: tuple[str, ...] = (
    "related_to",
    "works_at",
    "works_with",
    "reports_to",
    "manages",
    "leads",
    "founded",
    "owns",
    "part_of",
    "member_of",
    "instance_of",
    "has_property",
    "located_in",
    "lives_in",
    "born_in",
    "occurs_at",
    "attends",
    "participates_in",
    "created",
    "authored",
    "produces",
    "uses",
    "depends_on",
    "mentions",
    "discusses",
    "causes",
    "precedes",
    "follows",
    "knows",
    "friend_of",
    "married_to",
    "parent_of",
    "child_of",
    "sibling_of",
    "studied_at",
    "teaches",
    "competes_with",
    "partners_with",
    "invests_in",
    "funds",
    "sells",
    "buys",
)


class ExtractedRelationship(BaseModel):
    """Relationship between two nodes extracted from content."""

    source_name: str = ""
    target_name: str = ""
    relationship_type: str = "related_to"
    natural_language: str = ""

    @field_validator("source_name", "target_name", "natural_language", mode="before")
    @classmethod
    def handle_none_strings(cls, v: Any) -> Any:
        return "" if v is None else v

    @field_validator("relationship_type", mode="before")
    @classmethod
    def closed_vocabulary(cls, v: Any) -> str:
        """Normalise spelling, then reject anything off the list — no fuzzy matching."""
        key = "_".join(str(v or "").strip().lower().split())
        return key if key in RELATIONSHIP_TYPES else "related_to"


class Extraction(BaseModel):
    """Root extraction result — nodes and relationships found in a note."""

    nodes: list[Node] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    title: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, data: Any) -> Any:
        """Accept messy LLM shapes (bare lists, wrappers, embedded rels)."""
        if data is None:
            return {"nodes": [], "relationships": []}

        # Unwrap common outer wrappers
        if isinstance(data, dict):
            for key in ("extraction", "data", "result"):
                inner = data.get(key)
                if isinstance(inner, dict) and (
                    "nodes" in inner or "relationships" in inner
                ):
                    data = inner
                    break

        # Gemma-style: [nodes_list, relationships_list]
        if (
            isinstance(data, list)
            and len(data) == 2
            and isinstance(data[0], list)
            and isinstance(data[1], list)
        ):
            data = {"nodes": data[0], "relationships": data[1]}

        # Bare list of nodes (dicts or strings), optionally with embedded rels
        if isinstance(data, list):
            nodes: list[Any] = []
            relationships: list[Any] = []
            for item in data:
                if isinstance(item, str) and item.strip():
                    nodes.append({"name": item.strip()})
                    continue
                if not isinstance(item, dict):
                    continue
                node = dict(item)
                embedded = node.pop("relationships", None)
                if isinstance(embedded, list):
                    relationships.extend(embedded)
                nodes.append(node)
            data = {"nodes": nodes, "relationships": relationships}

        if not isinstance(data, dict):
            return {"nodes": [], "relationships": []}
        return data

    @field_validator("nodes", "relationships", mode="before")
    @classmethod
    def ensure_list(cls, v: Any, info) -> list:
        """Coerce None or scalars to a list; string items → minimal nodes."""
        if v is None or not isinstance(v, list):
            return []
        if info.field_name == "nodes":
            return [
                (
                    {"name": item.strip()}
                    if isinstance(item, str) and item.strip()
                    else item
                )
                for item in v
            ]
        return v


class NoteInput(BaseModel):
    """Create or re-ingest a note (legacy ``POST /ingest`` + pipeline)."""

    content: str
    created_at: str | None = None
    title: str | None = None  # If provided, use instead of auto-generating
    skip_ingestion: bool = False  # Save metadata/vault only; skip graph ingest


class EntityPass(BaseModel):
    """Pass 1 of task-split extraction: what exists, over the whole note.

    Names and types only. The output is small enough that a long note fits in
    one call, which is the point — every later pass then works from one
    complete, canonical entity list instead of rediscovering entities per
    chunk and hoping the names match.
    """

    title: str | None = None
    nodes: list[Node] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, data: Any) -> Any:
        if data is None:
            return {"nodes": []}
        if isinstance(data, list):
            return {"nodes": data}
        if isinstance(data, dict):
            for key in ("entities", "nodes", "result", "data"):
                if key in data and isinstance(data[key], list):
                    return {"title": data.get("title"), "nodes": data[key]}
        return data


class RelationshipPass(BaseModel):
    """Pass 2: how the entities connect, with the full entity list in context.

    Seeing the whole note and every entity at once is what makes a relationship
    across two distant sections expressible at all — under chunking neither
    side could state it.
    """

    relationships: list[ExtractedRelationship] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, data: Any) -> Any:
        if data is None:
            return {"relationships": []}
        if isinstance(data, list):
            return {"relationships": data}
        if isinstance(data, dict):
            for key in ("relationships", "edges", "result", "data"):
                if key in data and isinstance(data[key], list):
                    return {"relationships": data[key]}
        return data


class NodeContext(BaseModel):
    """One entity's description, from pass 3."""

    name: str = ""
    isolated_context: str = ""

    @field_validator("name", "isolated_context", mode="before")
    @classmethod
    def handle_none_strings(cls, v: Any) -> Any:
        return "" if v is None else v


class ContextPass(BaseModel):
    """Pass 3: descriptions for known entities.

    This is the pass that may still be chunked, and safely so — a description
    is drawn from where the entity appears, and the entity list is already
    fixed, so chunking here cannot invent or split an entity.
    """

    contexts: list[NodeContext] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, data: Any) -> Any:
        if data is None:
            return {"contexts": []}
        if isinstance(data, list):
            return {"contexts": data}
        if isinstance(data, dict):
            for key in ("contexts", "nodes", "entities", "result", "data"):
                if key in data and isinstance(data[key], list):
                    return {"contexts": data[key]}
        return data
