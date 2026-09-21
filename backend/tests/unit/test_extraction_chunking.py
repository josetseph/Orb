"""Unit tests for paragraph-bounded extraction chunking and result merging."""

from app.schemas.extraction import ExtractedRelationship, Extraction, Node
from app.core import config
from app.workflows.extraction_chunking import (
    MIN_SPLIT_TOKENS,
    chunk_token_budget,
    merge_extractions,
    split_for_extraction,
)


def _words(text: str) -> int:
    """Deterministic stand-in tokenizer: one token per whitespace-separated word."""
    return len(text.split())


class TestSplitForExtraction:
    def test_short_text_is_returned_unchanged(self):
        assert split_for_extraction("a b c", 10, _words) == ["a b c"]

    def test_empty_text_yields_no_chunks(self):
        assert split_for_extraction("   ", 10, _words) == []

    def test_splits_on_paragraph_boundaries_first(self):
        paras = [" ".join(["w"] * 6) for _ in range(4)]
        text = "\n\n".join(paras)
        chunks = split_for_extraction(text, 12, _words)
        # Two paragraphs (12 words) fit per chunk; none is cut mid-paragraph.
        assert len(chunks) == 2
        for chunk in chunks:
            assert _words(chunk) <= 12
            assert chunk.count("\n\n") == 1

    def test_oversized_paragraph_falls_back_to_sentences(self):
        sentences = [f"Sentence {i} has five words." for i in range(6)]
        text = " ".join(sentences)  # one paragraph, 30 words
        chunks = split_for_extraction(text, 10, _words)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert _words(chunk) <= 10
            assert chunk.endswith(".")

    def test_every_word_survives_the_split(self):
        text = "\n\n".join(" ".join(f"p{p}w{w}" for w in range(9)) for p in range(5))
        chunks = split_for_extraction(text, 20, _words)
        assert " ".join(" ".join(c.split()) for c in chunks).split() == text.split()

    def test_chunks_are_never_empty(self):
        text = "\n\n\n\n" + "x y z" + "\n\n\n"
        assert split_for_extraction(text, 2, _words) == ["x y", "z"]


class TestChunkTokenBudget:
    def test_budget_fits_prompt_and_expected_output(self, monkeypatch):
        monkeypatch.setattr(config.settings, "EXTRACTION_CHUNK_TOKENS", None)
        budget = chunk_token_budget(16384, 1900)
        # input + ~2.5× output must fit under the window
        assert budget * 3.5 <= 16384 - 1900
        assert budget >= MIN_SPLIT_TOKENS

    def test_large_context_is_capped(self, monkeypatch):
        monkeypatch.setattr(config.settings, "EXTRACTION_CHUNK_TOKENS", None)
        assert chunk_token_budget(128000, 2000) == 4000

    def test_env_override_raises_or_lowers_cap(self, monkeypatch):
        monkeypatch.setattr(config.settings, "EXTRACTION_CHUNK_TOKENS", 1500)
        assert chunk_token_budget(128000, 2000) == 1500

    def test_never_below_min_split(self, monkeypatch):
        monkeypatch.setattr(config.settings, "EXTRACTION_CHUNK_TOKENS", None)
        assert chunk_token_budget(1000, 2000) == MIN_SPLIT_TOKENS


def _node(name, context, type_="Person"):
    return Node(name=name, type=type_, isolated_context=context)


def _ext(nodes, relationships=(), title=None):
    return Extraction(nodes=list(nodes), relationships=list(relationships), title=title)


class TestMergeExtractions:
    """Chunks merge on the name exactly as written. Nothing is lower-cased or trimmed to make two names meet."""

    def test_the_same_name_merges_and_contexts_join_in_order(self):
        a = _ext([_node("Ama", "Ama is a girl.")], title="First")
        b = _ext([_node("Ama", "Ama plays on weekends."), _node("Kofi", "Kofi is a boy.")])
        merged = merge_extractions([a, b])
        assert [n.name for n in merged.nodes] == ["Ama", "Kofi"]
        assert merged.nodes[0].isolated_context == "Ama is a girl. Ama plays on weekends."
        assert merged.title == "First"

    def test_a_different_spelling_is_a_different_entity(self):
        merged = merge_extractions([_ext([_node("Ama", "x")]), _ext([_node("ama", "y")])])
        assert [n.name for n in merged.nodes] == ["Ama", "ama"]

    def test_duplicate_context_not_repeated(self):
        merged = merge_extractions([_ext([_node("X", "same")]), _ext([_node("X", "same")])])
        assert merged.nodes[0].isolated_context == "same"

    def test_the_first_type_stands(self):
        merged = merge_extractions([_ext([_node("Paris", "x", "City")]), _ext([_node("Paris", "y", "Place")])])
        assert merged.nodes[0].type == "City"

    def test_identical_relationships_dedupe_keeping_first(self):
        people = [_node("Ama", "x"), _node("Kofi", "y")]
        rel = dict(source_name="Ama", target_name="Kofi", relationship_type="friend_of")
        first = ExtractedRelationship(**rel, natural_language="a")
        later = ExtractedRelationship(**rel, natural_language="b")
        merged = merge_extractions([_ext(people, [first]), _ext(people, [later])])
        assert [r.natural_language for r in merged.relationships] == ["a"]

    def test_a_relationship_may_span_chunks(self):
        rel = ExtractedRelationship(source_name="Ama", target_name="Ama", relationship_type="related_to", natural_language="z")
        merged = merge_extractions([_ext([_node("Ama", "x")], [rel]), _ext([_node("Kofi", "y")])])
        assert len(merged.relationships) == 1 and len(merged.nodes) == 2
