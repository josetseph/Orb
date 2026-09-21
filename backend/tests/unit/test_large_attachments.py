"""Recordings and long documents become notes: a graphed summary plus searchable full text."""

import asyncio
from types import SimpleNamespace

from app.workflows.agents import ingestion_agent as ia
from app.workflows.ingestion import _passages

LINK = "attachments/Book%20%282%29-a45eb768.pdf"
KEY = ia.attachment_key(LINK)
NOTES_BODY = "[PDF Extraction (Book)]:\n## Summary\nshort version\n\n## Full text\nlong long text"


def _note(mode: str, body: str = NOTES_BODY) -> str:
    return f"mine\n[📎 Book]({LINK})\n\n{ia.extract_open(LINK, mode)}\n{body}\n{ia.EXTRACT_CLOSE}\n\nafter"


def test_marker_round_trips_with_and_without_a_mode():
    for mode in ("", "notes"):
        (block,) = ia.extraction_blocks(_note(mode))
        assert (block["key"], block["mode"]) == (KEY, mode)
        assert ia.extraction_srcs(_note(mode)) == {KEY}


def test_a_notes_block_contributes_its_summary_only():
    out = ia.graph_text(_note("notes"))
    assert "short version" in out and "long long text" not in out
    assert "mine" in out and "after" in out
    assert "long long text" in ia.graph_text(_note(""))  # a plain block is graphed whole


def test_split_notes_handles_both_headings_and_plain_bodies():
    assert ia.split_notes(NOTES_BODY) == ("[PDF Extraction (Book)]:\n## Summary\nshort version", "long long text")
    assert ia.split_notes("# T\n\n## Summary\ns\n\n## Transcript\n[00:01] Speaker 1: hi")[1] == "[00:01] Speaker 1: hi"
    assert ia.split_notes("no sections here") == ("no sections here", "")


def test_strip_keeps_a_notes_block_while_its_link_remains():
    assert 'mode="notes"' in ia._strip_prior_multimedia_enrichment(_note("notes"), keep={KEY})
    assert "orb:extract" not in ia._strip_prior_multimedia_enrichment(_note("notes"), keep=set())


def test_passages_split_on_paragraphs_and_break_up_giants():
    text = "\n".join(["p" * 500] * 6) + "\n" + "g" * 5000
    out = _passages(text)
    assert all(len(p) <= 2400 for p in out) and len(out) >= 5
    assert "".join(out).replace("\n", "").count("g") == 5000
    assert _passages("  \n \n") == []


class _LLM:
    def __init__(self):
        self.calls = []

    def get_ingestion_model(self):
        return "stub"

    def ingestion_count_tokens(self, text):
        return len(text.split())

    async def summarize_document(self, filename, text):
        self.calls.append(("document", filename))
        return f"short version of {filename}"

    async def summarize_transcript(self, filename, text):
        self.calls.append(("transcript", filename, text))
        return f"# Title\n\n## Summary\n### Flow Summary\nabout {filename}"


async def _status(*a, **k):
    return None


def _finish(monkeypatch, kind, section, limit=5):
    monkeypatch.setattr(ia.settings, "LARGE_ATTACHMENT_TOKENS", limit)
    llm = _LLM()
    return llm, asyncio.run(ia.finish_attachment(kind, section, "f.ext", llm, _status))


def test_a_recording_always_gets_notes_with_its_timed_transcript(monkeypatch):
    timed = "[00:01] Speaker 1: Good morning.\n[00:04] Speaker 1: Let's begin.\n[00:09] Speaker 2: Okay."
    llm, (body, mode) = _finish(monkeypatch, "audio", f"\n\n[Audio Transcript (f.ext)]: {timed}", limit=10_000)
    assert mode == "notes"
    summary, transcript = ia.split_notes(body)
    assert summary.startswith("[Audio Transcript (f.ext)]:\n# Title") and transcript == timed
    # The summariser reads speaker paragraphs, not stamped lines.
    assert llm.calls == [("transcript", "f.ext", "Speaker 1: Good morning. Let's begin.\n\nSpeaker 2: Okay.")]


def test_a_long_document_is_summarised_and_a_short_one_is_not(monkeypatch):
    llm, (body, mode) = _finish(monkeypatch, "pdf", "\n\n[PDF Extraction (My [v2] file.pdf)]: " + "word " * 50)
    assert mode == "notes" and llm.calls == [("document", "f.ext")]
    summary, full = ia.split_notes(body)
    assert summary == "[PDF Extraction (My [v2] file.pdf)]:\n## Summary\nshort version of f.ext"
    assert full.startswith("word word")

    llm, (body, mode) = _finish(monkeypatch, "pdf", "\n\n[PDF Extraction (f)]: tiny", limit=10_000)
    assert (mode, llm.calls) == ("", []) and body.endswith("tiny")


def test_images_are_never_summarised(monkeypatch):
    llm, (_, mode) = _finish(monkeypatch, "image", "[Image: x]\n" + "word " * 50)
    assert (mode, llm.calls) == ("", [])


def test_full_ingest_and_process_this_item_share_the_rule(monkeypatch):
    from app.api import notes as notes_api
    from app.schemas.extraction import NoteInput
    import app.core.database as database

    docx = "attachments/Book-11111111.docx"
    note_md = f"intro\n\n[📎 Book.docx]({docx})\n\noutro"

    async def fake_extract(kind, item, set_status, llm):
        return "[Word Extraction (Book.docx)]: " + "word " * 50

    monkeypatch.setattr(ia, "extract_attachment", fake_extract)
    monkeypatch.setattr(ia.settings, "LARGE_ATTACHMENT_TOKENS", 5)
    monkeypatch.setattr(ia.multimedia_service, "extract_docx_images", lambda url: [])
    saved = {}

    class WF:
        _llm = _LLM()
        kb_id = "kb"

        async def _update_note_processing_status(self, *a, **k):
            return None

        async def _persist_note_body(self, note_id, body):
            saved["body"] = body

    state = {"input": NoteInput(content=note_md), "logs": [], "workflow": WF(), "note_id": "n1"}
    out = asyncio.run(ia.multimodal_node(state))["content"]
    assert ia.extraction_blocks(out)[0]["mode"] == "notes"
    assert "word word" not in ia.graph_text(out) and "short version" in ia.graph_text(out)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _q):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id="n1", kb_id="kb"))

    monkeypatch.setattr(database, "AsyncSessionLocal", Session)
    monkeypatch.setattr(notes_api, "note_body", lambda note, kb: note_md)
    kb = SimpleNamespace(kb_id="kb", get_ingestion_workflow=lambda: WF())
    asyncio.run(notes_api._run_attachment_job(kb, "n1", f"/vault-files/kb/{docx}"))
    assert ia.extraction_blocks(saved["body"])[0]["mode"] == "notes"


def test_process_this_item_runs_a_video_as_two_passes(monkeypatch):
    """The audio pass gets notes; the visual pass keeps its own block."""
    from app.api import notes as notes_api
    import app.core.database as database

    mov = "attachments/Talk-22222222.mov"
    note_md = f"intro\n\n[📎 Talk.mov]({mov})\n\noutro"
    seen = []

    async def fake_extract(kind, item, set_status, llm):
        seen.append(kind)
        if kind == "video_audio":
            return "[Video Audio Transcript (Talk.mov)]: [00:01] Speaker 1: Hello."
        return "[Video Visual Analysis (Talk.mov)]:\n\na slide"

    monkeypatch.setattr(ia, "extract_attachment", fake_extract)
    monkeypatch.setattr(ia.multimedia_service, "unload_local_models", lambda family=None: None)
    monkeypatch.setattr(ia.multimedia_service, "unload_marlin", lambda: None)
    saved = {}

    class WF:
        _llm = _LLM()

        async def _persist_note_body(self, note_id, body):
            saved["body"] = body

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _q):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id="n1", kb_id="kb"))

    monkeypatch.setattr(database, "AsyncSessionLocal", Session)
    monkeypatch.setattr(notes_api, "note_body", lambda note, kb: note_md)
    kb = SimpleNamespace(kb_id="kb", get_ingestion_workflow=lambda: WF())
    asyncio.run(notes_api._run_attachment_job(kb, "n1", f"/vault-files/kb/{mov}"))

    assert seen == ["video_audio", "video_visual"]
    modes = sorted(b["mode"] for b in ia.extraction_blocks(saved["body"]))
    assert modes == ["", "notes"]
    assert "[00:01] Speaker 1: Hello." in saved["body"] and "a slide" in ia.graph_text(saved["body"])
