"""Large attachments are parked until the user picks graph / summary / index."""

import asyncio

from app.workflows.agents import ingestion_agent as ia
from app.workflows.ingestion import _passages

LINK = "attachments/Book%20%282%29-a45eb768.pdf"
KEY = ia.attachment_key(LINK)


def _note(mode: str, body: str = "[PDF Extraction (Book)]: text") -> str:
    return f"mine\n[📎 Book]({LINK})\n\n{ia.extract_open(LINK, mode)}\n{body}\n{ia.EXTRACT_CLOSE}\n\nafter"


def test_marker_round_trips_with_and_without_a_mode():
    for mode in ("", "pending", "index", "summary"):
        (block,) = ia.extraction_blocks(_note(mode))
        assert (block["key"], block["mode"]) == (KEY, mode)
        assert ia.extraction_srcs(_note(mode)) == {KEY}  # parked still counts as processed


def test_only_graphable_text_reaches_extraction():
    assert "text" in ia.graph_text(_note(""))
    assert "text" in ia.graph_text(_note("summary"))
    for mode in ia.UNGRAPHED_MODES:
        out = ia.graph_text(_note(mode))
        assert "text" not in out and "mine" in out and "after" in out


def test_set_block_mode_rewrites_one_block_only():
    other = f'{ia.extract_open("attachments/o.pdf")}\nkeep\n{ia.EXTRACT_CLOSE}'
    out = ia.set_block_mode(_note("pending") + "\n" + other, KEY, "index")
    modes = {b["key"]: b["mode"] for b in ia.extraction_blocks(out)}
    assert modes == {KEY: "index", "attachments/o.pdf": ""}
    out = ia.set_block_mode(_note("pending"), KEY, "summary", "[Summary (Book)]: short")
    assert ia.extraction_blocks(out)[0]["body"] == "[Summary (Book)]: short"
    assert ia.set_block_mode(_note("pending"), KEY, "").count('mode="') == 0


def test_strip_keeps_a_parked_block_while_its_link_remains():
    kept = ia._strip_prior_multimedia_enrichment(_note("pending"), keep={KEY})
    assert 'mode="pending"' in kept
    assert "orb:extract" not in ia._strip_prior_multimedia_enrichment(_note("pending"), keep=set())


def test_passages_split_on_paragraphs_and_break_up_giants():
    text = "\n".join(["p" * 500] * 6) + "\n" + "g" * 5000
    out = _passages(text)
    assert all(len(p) <= 2400 for p in out) and len(out) >= 5
    assert "".join(out).replace("\n", "").count("g") == 5000
    assert _passages("  \n \n") == []


class _LLM:
    def get_ingestion_model(self):
        return "stub"

    def ingestion_count_tokens(self, text):
        return len(text.split())

    async def summarize_document(self, filename, text):
        return f"short version of {filename}"


def _run(monkeypatch, content, decisions, tokens_limit=5, extractor_must_not_run=False):
    async def fake_extract(kind, item, set_status, llm):
        assert not extractor_must_not_run, "a parked attachment was extracted again"
        return "[Word Extraction (Book.docx)]: " + "word " * 50

    monkeypatch.setattr(ia, "extract_attachment", fake_extract)
    monkeypatch.setattr(ia.settings, "LARGE_ATTACHMENT_TOKENS", tokens_limit)
    monkeypatch.setattr(ia.multimedia_service, "extract_docx_images", lambda url: [])
    saved = {}

    class WF:
        _llm = _LLM()
        kb_id = "kb"

        async def _attachment_modes(self, note_id):
            return decisions

        async def _update_note_processing_status(self, *a, **k):
            return None

        async def _persist_note_body(self, note_id, body):
            saved["body"] = body

    from app.schemas.extraction import NoteInput

    state = {"input": NoteInput(content=content), "logs": [], "workflow": WF(), "note_id": "n1"}
    return asyncio.run(ia.multimodal_node(state))["content"]


DOCX = "attachments/Book-11111111.docx"
DOC_NOTE = f"intro\n\n[📎 Book.docx]({DOCX})\n\noutro"


def test_large_attachment_is_parked_with_its_text_until_answered(monkeypatch):
    out = _run(monkeypatch, DOC_NOTE, {})
    (block,) = ia.extraction_blocks(out)
    assert block["mode"] == "pending" and "word word" in block["body"]
    assert "word word" not in ia.graph_text(out) and "intro" in ia.graph_text(out)


def test_small_attachment_is_graphed_without_asking(monkeypatch):
    out = _run(monkeypatch, DOC_NOTE, {}, tokens_limit=10_000)
    assert ia.extraction_blocks(out)[0]["mode"] == ""


def test_answers_are_applied_to_new_and_to_parked_attachments(monkeypatch):
    key = ia.attachment_key(DOCX)
    assert ia.extraction_blocks(_run(monkeypatch, DOC_NOTE, {key: "index"}))[0]["mode"] == "index"
    assert ia.extraction_blocks(_run(monkeypatch, DOC_NOTE, {key: "graph"}))[0]["mode"] == ""

    parked = _run(monkeypatch, DOC_NOTE, {})
    out = _run(monkeypatch, parked, {key: "summary"}, extractor_must_not_run=True)
    (block,) = ia.extraction_blocks(out)
    assert block["mode"] == "summary"
    assert block["body"] == "[Summary (Book-11111111.docx)]: short version of Book-11111111.docx"
