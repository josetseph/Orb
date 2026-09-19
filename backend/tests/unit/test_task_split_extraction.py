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

    def __init__(
        self, entities, relationships, contexts=None, truncated=False, context_tokens=128000
    ):
        self.entities, self.relationships = entities, relationships
        self.contexts = contexts or {}
        self.truncated = truncated
        self.context_tokens = context_tokens
        self.prompts = []

    def _clean_json(self, raw):
        return raw

    def get_ingestion_model(self):
        return "fake-model"

    def ingestion_count_tokens(self, text):
        return len(text or "") // 4 + 1

    def ingestion_context_tokens(self):
        return self.context_tokens

    async def ingestion_generate_with_meta(self, prompt, temperature=0.1, **kw):
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

    def test_json_templates_render_single_braces(self):
        """The f-string templates once doubled ``{{`` — the model was shown ``{{``."""
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "x"})
        _run(llm, "note", budget=10_000)
        for prompt in llm.prompts:
            assert "{{" not in prompt and "}}" not in prompt
            assert '"name"' in prompt or '"source_name"' in prompt

    def test_relationship_pass_lists_the_closed_vocabulary(self):
        llm = FakeLLM(ENTITIES, RELS, {})
        _run(llm, "note", budget=10_000)
        assert "lives_in" in llm.prompts[1] and "related_to" in llm.prompts[1]

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

    def test_context_pass_splits_only_past_the_context_window(self):
        """It splits when the document genuinely will not fit — and only then.

        The entity list is already fixed by pass 1, so a split here cannot
        invent an entity or strand one across two spellings.
        """
        note = "\n\n".join(
            f"Paragraph {i} discusses Ama at some length here." for i in range(200)
        )
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl"}, context_tokens=1_500)
        _, calls = _run(llm, note, budget=20)
        assert calls > 3, "a document past the window must be split"

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
            async def ingestion_generate_with_meta(self, prompt, temperature=0.1, **kw):
                if "relationship extraction engine" in prompt:
                    raise RuntimeError("endpoint fell over")
                return await super().ingestion_generate_with_meta(prompt, temperature, **kw)

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


class TestContextPassCost:
    """The context pass multiplies pieces by entity batches — the one place
    task-splitting can end up more expensive than the chunking it replaced."""

    def test_only_entities_present_in_a_piece_are_asked_about(self):
        note = "Ama appears in this first paragraph only.\n\n" + "\n\n".join(
            f"Paragraph {i} is about Kofi and says a fair amount." for i in range(200)
        )
        llm = FakeLLM(
            ENTITIES, RELS, {"Ama": "a girl", "Kofi": "a boy"}, context_tokens=1_200
        )
        _run(llm, note, budget=20)
        ctx_prompts = [p for p in llm.prompts if "context extraction engine" in p]
        # Ama is named once; she must not be asked about in every Kofi piece.
        ama = sum(1 for p in ctx_prompts if "- Ama" in p)
        kofi = sum(1 for p in ctx_prompts if "- Kofi" in p)
        assert ama < kofi, "absent entities should be skipped for that piece"
        assert ama >= 1, "but still asked where she does appear"

    def test_a_single_piece_asks_about_everything(self):
        """No filtering when the text is not split — nothing to save."""
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl", "Kofi": "a boy"})
        _run(llm, "Short note.", budget=10_000)
        ctx = [p for p in llm.prompts if "context extraction engine" in p]
        assert len(ctx) == 1
        assert "- Ama" in ctx[0] and "- Kofi" in ctx[0]

    def test_call_count_beats_plain_chunking(self):
        """The regression this guards: pieces x batches without filtering."""
        note = "\n\n".join(
            f"Paragraph {i} mentions Ama and continues for a while."
            if i % 10 == 0
            else f"Paragraph {i} says something unrelated at length."
            for i in range(200)
        )
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl"}, context_tokens=1_200)
        _, calls = _run(llm, note, budget=30)
        pieces = len(note) // (30 * 4) + 1
        assert calls < 2 + pieces * 2, f"{calls} calls is too many for {pieces} pieces"


class TestContextPassSeesTheWholeDocument:
    """The context pass is limited by the context window, not by output.

    Sizing it with the extraction budget split documents that fit whole, and
    every extra piece costs a call per entity batch.
    """

    def test_budget_is_far_larger_than_the_extraction_budget(self):
        from app.workflows.extraction_chunking import chunk_token_budget

        extraction = chunk_token_budget(128_000, 1_500)
        contexts = ia.context_pass_budget(128_000, 400, ia._CONTEXT_BATCH)
        assert contexts > extraction * 10

    def test_a_long_document_still_goes_in_whole(self):
        """~84k tokens: split under the extraction budget, one piece here."""
        note = "\n\n".join(f"Paragraph {i} about Ama and Kofi." for i in range(4000))
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl", "Kofi": "a boy"})
        _, calls = _run(llm, note, budget=26_214)
        ctx = [p for p in llm.prompts if "context extraction engine" in p]
        assert len(ctx) == 1, "the document should not be split for contexts"
        assert note in ctx[0], "and the call should carry all of it"
        assert calls == 3

    def test_batch_size_shrinks_the_budget(self):
        small = ia.context_pass_budget(128_000, 400, 10)
        large = ia.context_pass_budget(128_000, 400, 500)
        assert small > large, "more entities per call leaves less room for text"

    def test_a_tiny_context_window_still_yields_a_usable_budget(self):
        assert ia.context_pass_budget(4_000, 400, 100) >= 400


class TestEntityNameMatching:
    """A pass echoing the list back must still resolve to the right entity.

    The real failure: the context prompt listed "Name (Type)" and asked for the
    name "exactly as listed", so the model returned the parenthetical too. Every
    description then matched nothing and was discarded — 135 entities described,
    0 attached, with no error anywhere.
    """

    KNOWN = {"masters in intelligent computing systems", "ghana"}

    def test_exact_name_matches(self):
        assert ia.match_entity_name("Ghana", self.KNOWN) == "ghana"

    def test_echoed_type_suffix_matches(self):
        got = ia.match_entity_name(
            "Masters in Intelligent Computing Systems (Program)", self.KNOWN
        )
        assert got == "masters in intelligent computing systems"

    def test_bullet_and_quotes_are_tolerated(self):
        assert ia.match_entity_name('- "Ghana"', self.KNOWN) == "ghana"

    def test_a_genuinely_unknown_entity_is_rejected(self):
        assert ia.match_entity_name("Atlantis (Place)", self.KNOWN) is None

    def test_empty_is_rejected(self):
        assert ia.match_entity_name("", self.KNOWN) is None
        assert ia.match_entity_name(None, self.KNOWN) is None

    def test_context_prompt_lists_bare_names(self):
        """Not 'Name (Type)' — that is what invited the echo."""
        llm = FakeLLM(ENTITIES, RELS, {"Ama": "a girl"})
        _run(llm, "note", budget=10_000)
        ctx = [p for p in llm.prompts if "context extraction engine" in p][0]
        assert "- Ama\n" in ctx or ctx.rstrip().endswith("- Ama")
        assert "- Ama (Person)" not in ctx

    def test_descriptions_attach_when_the_model_echoes_the_type(self):
        """End to end: the exact shape that produced 0/135."""

        class EchoingLLM(FakeLLM):
            async def ingestion_generate_with_meta(self, prompt, temperature=0.1, **kw):
                if "context extraction engine" in prompt:
                    self.prompts.append(prompt)
                    return (
                        json.dumps(
                            {
                                "contexts": [
                                    {
                                        "name": "Ama (Person)",
                                        "isolated_context": "a girl",
                                    }
                                ]
                            }
                        ),
                        {},
                    )
                return await super().ingestion_generate_with_meta(prompt, temperature, **kw)

        llm = EchoingLLM(ENTITIES, RELS, {})
        result, _ = _run(llm, "note", budget=10_000)
        described = {n.name: n.isolated_context for n in result.nodes}
        assert described["Ama"] == "a girl", "echoed type must still attach"

    def test_relationships_tolerate_the_same_echo(self):
        rels = [
            {
                "source_name": "Ama (Person)",
                "target_name": "Kofi (Person)",
                "relationship_type": "knows",
                "natural_language": "x",
            }
        ]
        llm = FakeLLM(ENTITIES, rels, {})
        result, _ = _run(llm, "note", budget=10_000)
        assert len(result.relationships) == 1
