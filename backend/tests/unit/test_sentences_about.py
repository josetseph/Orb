"""An entity with no model-written context gets the sentences about it, not the note."""

from app.workflows.extraction_chunking import sentences_about

NOTE = (
    "The lecture covered tooling. Perplexity was suggested for literature search! "
    "Team 1 presented first. Later, the instructor compared Perplexity with plain Google.\n"
    "Nothing else mentioned it. " + "Filler sentence. " * 200
)


def test_only_sentences_naming_the_entity_in_order():
    out = sentences_about("Perplexity", NOTE)
    assert out == (
        "Perplexity was suggested for literature search! "
        "Later, the instructor compared Perplexity with plain Google."
    )


def test_case_insensitive_and_capped():
    text = " ".join(f"perplexity appears in sentence {i}." for i in range(20))
    out = sentences_about("Perplexity", text, max_sentences=3)
    assert out.count("perplexity") == 3


def test_absent_entity_gives_nothing_not_the_note():
    assert sentences_about("Zebra", NOTE) == ""
    assert sentences_about("", NOTE) == ""


def test_monster_sentence_is_windowed():
    text = "x " * 5000 + "Perplexity here " + "y " * 5000
    out = sentences_about("Perplexity", text, max_chars=200)
    assert "Perplexity here" in out and len(out) <= 210


def test_canonical_name_falls_back_to_its_words():
    text = "We discussed the waterfall approach in depth. Agile came next. The Nokia phone rang."
    assert sentences_about("Waterfall Model", text) == "We discussed the waterfall approach in depth."
    assert sentences_about("Nokia 3310", text) == "The Nokia phone rang."
    assert sentences_about("Quantum Field Theory", text) == ""
