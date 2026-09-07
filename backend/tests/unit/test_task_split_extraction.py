"""Large notes extract by task, not by text, so every pass sees the whole note.

Output size is what stops a long note going up in one call. Splitting by task
shrinks each response enough that entities and relationships are found with the
entire document in view — so a relationship spanning two distant sections is
expressible, which chunking made impossible.
"""

import asyncio
import json

import importlib

# The agents package re-exports a compiled graph named ``ingestion_agent``,
# which shadows the submodule of the same name.
ia = importlib.import_module("app.workflows.agents.ingestion_agent")


class FakeLLM:
    """Answers each pass by looking at which prompt it was handed."""

    def __init__(self, entities, relationships, contexts=None, truncated=False):
        self.entities, self.relationships = entities, relationships
        self.contexts = contexts or {}
        self.truncated = truncated
        self.prompts = []

    def _clean_json(self, raw):
        return raw

    def get_ingestion_model(self):
        return "fake-model"

    def ingestion_count_tokens(self, text):
        return len(text or "") // 4 + 1

    def ingestion_context_tokens(self):
        return 128000

    async def ingestion_generate_with_meta(self, prompt, temperature=0.1):
        self.prompts.append(prompt)
        meta = {"truncated": self.truncated}
        if "entity extraction engine" in prompt:
            return json.dumps({"title": "T", "nodes": self.entities}), meta
        if "relationship extraction engine" in prompt:
            return json.dumps({"relationships": self.relationships}), meta
        listed = [
            line[2:].split(" (")[0]
            for line in prompt.split("ENTITIES:\n", 1)[-1].splitlines()
            if line.startswith("- ")
        ]
        return (
            json.dumps(
                {
                    "contexts": [
                        {"name": n, "isolated_context": self.contexts[n]}
                        for n in listed
                        if n in self.contexts
                    ]
                }
            ),
            meta,
        )


ENTITIES = [{"name": "Ama", "type": "Person"}, {"name": "Kofi", "type": "Person"}]
RELS = [
    {
        "source_name": "Ama",
        "target_name": "Kofi",
        "relationship_type": "is_friends_with",
        "natural_language": "friends",
    }
]


def _run(llm, content, budget=10):
    return asyncio.run(
        ia._extract_task_split(llm, content, llm.ingestion_count_tokens, budget, [])
    )


class TestPassStructure:
    def test_three_passes_for_a_small_entity_set(self):
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl", "Kofi": "a boy"})
        result, calls = _run(llm, "Ama and Kofi are friends.", budget=10_000)
        assert calls == 3
        assert len(result.nodes) == 2 and len(result.relationships) == 1

    def test_every_pass_receives_the_whole_note(self):
        """The point of task-splitting: no pass sees a fragment."""
        note = "Ama is here. " * 200
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "x", "Kofi": "y"})
        _run(llm, note, budget=10_000)
        assert note in llm.prompts[0], "entity pass must see the whole note"
        assert note in llm.prompts[1], "relationship pass must see the whole note"

    def test_relationship_pass_is_given_the_entity_list(self):
        llm = FakeLLM(ENTITIES, RELS, {})
        _run(llm, "note", budget=10_000)
        assert "- Ama (Person)" in llm.prompts[1]
        assert "- Kofi (Person)" in llm.prompts[1]

    def test_title_comes_from_the_entity_pass(self):
        llm = FakeLLM(ENTITIES, RELS, {})
        result, _ = _run(llm, "note", budget=10_000)
        assert result.title == "T"


class TestRelationshipsAreGrounded:
    def test_relationships_to_unknown_entities_are_dropped(self):
        rels = RELS + [
            {
                "source_name": "Ama",
                "target_name": "Someone Invented",
                "relationship_type": "knows",
                "natural_language": "?",
            }
        ]
        llm = FakeLLM(ENTITIES, rels, {})
        result, _ = _run(llm, "note", budget=10_000)
        assert len(result.relationships) == 1

    def test_matching_ignores_case_and_spacing(self):
        rels = [
            {
                "source_name": "  AMA ",
                "target_name": "kofi",
                "relationship_type": "knows",
                "natural_language": "x",
            }
        ]
        llm = FakeLLM(ENTITIES, rels, {})
        result, _ = _run(llm, "note", budget=10_000)
        assert len(result.relationships) == 1, "same normaliser as merge_extractions"


class TestContexts:
    def test_contexts_attach_to_their_entity(self):
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl", "Kofi": "a boy"})
        result, _ = _run(llm, "note", budget=10_000)
        by_name = {n.name: n.isolated_context for n in result.nodes}
        assert by_name["Ama"] == "a girl" and by_name["Kofi"] == "a boy"

    def test_context_pass_chunks_the_text_when_needed(self):
        """Only this pass may chunk — the entity list is already fixed."""
        note = "\n\n".join(f"Paragraph {i} about Ama." for i in range(40))
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl"})
        _, calls = _run(llm, note, budget=20)
        assert calls > 3, "long text should need several context calls"

    def test_an_entity_with_nothing_said_about_it_keeps_empty_context(self):
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl"})
        result, _ = _run(llm, "note", budget=10_000)
        assert {n.name: n.isolated_context for n in result.nodes}["Kofi"] == ""


class TestResilience:
    def test_no_entities_falls_back_to_chunking(self, monkeypatch):
        async def fake_chunks(llm, content, count, budget, logs):
            from app.schemas.extraction import Extraction, Node

            return Extraction(nodes=[Node(name="fallback", type="Thing")]), 2

        monkeypatch.setattr(ia, "_extract_by_chunks", fake_chunks)
        llm = FakeLLM([], [], {})
        result, _ = _run(llm, "note", budget=10_000)
        assert [n.name for n in result.nodes] == ["fallback"]

    def test_a_failing_pass_does_not_abort_the_note(self):
        class Broken(FakeLLM):
            async def ingestion_generate_with_meta(self, prompt, temperature=0.1):
                if "relationship extraction engine" in prompt:
                    raise RuntimeError("endpoint fell over")
                return await super().ingestion_generate_with_meta(prompt, temperature)

        llm = Broken(ENTITIES, RELS, {"Ama": "a girl"})
        result, _ = _run(llm, "note", budget=10_000)
        assert len(result.nodes) == 2, "entities survive a failed relationship pass"
        assert result.relationships == []


class TestRouting:
    def test_a_note_that_fits_stays_a_single_call(self, monkeypatch):
        seen = {}

        async def fake_chunks(llm, content, count, budget, logs):
            from app.schemas.extraction import Extraction

            seen["chunked"] = True
            return Extraction(), 1

        async def fake_split(*a, **k):
            from app.schemas.extraction import Extraction

            seen["split"] = True
            return Extraction(), 3

        monkeypatch.setattr(ia, "_extract_by_chunks", fake_chunks)
        monkeypatch.setattr(ia, "_extract_task_split", fake_split)
        llm = FakeLLM(ENTITIES, RELS, {})
        asyncio.run(ia._extract_with_chunking(llm, "short note", []))
        assert seen == {"chunked": True}, "small notes must not pay for 3 passes"

    def test_a_note_that_does_not_fit_is_task_split(self, monkeypatch):
        seen = {}

        async def fake_split(llm, content, count, budget, logs):
            from app.schemas.extraction import Extraction

            seen["split"] = True
            return Extraction(), 3

        monkeypatch.setattr(ia, "_extract_task_split", fake_split)
        monkeypatch.setattr(ia, "chunk_token_budget", lambda *a, **k: 5)
        llm = FakeLLM(ENTITIES, RELS, {})
        asyncio.run(ia._extract_with_chunking(llm, "a much longer note " * 50, []))
        assert seen.get("split") is True
