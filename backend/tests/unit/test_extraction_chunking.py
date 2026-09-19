"""Unit tests for paragraph-bounded extraction chunking and result merging."""

from app.schemas.extraction import ExtractedRelationship, Extraction, Node
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
        monkeypatch.delenv("ORB_EXTRACTION_CHUNK_TOKENS", raising=False)
        budget = chunk_token_budget(16384, 1900)
        # input + ~2.5× output must fit under the window
        assert budget * 3.5 <= 16384 - 1900
        assert budget >= MIN_SPLIT_TOKENS

    def test_large_context_is_capped(self, monkeypatch):
        monkeypatch.delenv("ORB_EXTRACTION_CHUNK_TOKENS", raising=False)
        assert chunk_token_budget(128000, 2000) == 4000

    def test_env_override_raises_or_lowers_cap(self, monkeypatch):
        monkeypatch.setenv("ORB_EXTRACTION_CHUNK_TOKENS", "1500")
        assert chunk_token_budget(128000, 2000) == 1500

    def test_never_below_min_split(self, monkeypatch):
        monkeypatch.delenv("ORB_EXTRACTION_CHUNK_TOKENS", raising=False)
        assert chunk_token_budget(1000, 2000) == MIN_SPLIT_TOKENS


class TestMergeExtractions:
    def test_nodes_dedupe_by_name_and_contexts_concatenate(self):
        a = Extraction(
            title="First",
            nodes=[Node(name="Ama", type="Person", isolated_context="Ama is a girl.")],
        )
        b = Extraction(
            nodes=[
                Node(name="ama", type="Person", isolated_context="Ama plays on weekends."),
                Node(name="Kofi", type="Person", isolated_context="Kofi is a boy."),
            ]
        )
        merged = merge_extractions([a, b])
        names = {n.name.lower() for n in merged.nodes}
        assert names == {"ama", "kofi"}
        ama = next(n for n in merged.nodes if n.name.lower() == "ama")
        assert ama.isolated_context == "Ama is a girl. Ama plays on weekends."
        assert merged.title == "First"

    def test_duplicate_context_not_repeated(self):
        node = Node(name="X", isolated_context="same")
        merged = merge_extractions([Extraction(nodes=[node]), Extraction(nodes=[node])])
        assert merged.nodes[0].isolated_context == "same"

    def test_generic_type_upgraded_by_later_chunk(self):
        a = Extraction(nodes=[Node(name="Paris", type="thing")])
        b = Extraction(nodes=[Node(name="Paris", type="Place")])
        assert merge_extractions([a, b]).nodes[0].type == "Place"

    def test_relationships_dedupe_keeping_first(self):
        first = ExtractedRelationship(
            source_name="Ama", target_name="Kofi", relationship_type="is_friends_with", natural_language="a"
        )
        later = ExtractedRelationship(
            source_name="ama", target_name="kofi", relationship_type="IS_FRIENDS_WITH", natural_language="b"
        )
        merged = merge_extractions([Extraction(relationships=[first]), Extraction(relationships=[later])])
        assert len(merged.relationships) == 1
        assert merged.relationships[0].natural_language == "a"

    def test_none_parts_are_skipped(self):
        merged = merge_extractions([None, Extraction(nodes=[Node(name="A")])])
        assert [n.name for n in merged.nodes] == ["A"]
