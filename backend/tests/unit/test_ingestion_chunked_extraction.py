"""Unit tests for chunked extraction and batched image titling in the ingestion agent.

The LLM is a stub: it "truncates" whenever a chunk is larger than a threshold,
so the agent must split and merge instead of accepting a broken JSON blob.
"""

import importlib
import json

import pytest

# The package re-exports the compiled graph as ``ingestion_agent``, shadowing the
# module attribute — load the module itself.
agent = importlib.import_module("app.workflows.agents.ingestion_agent")


class _StubLLM:
    """Word-count tokenizer; truncates outputs for inputs above ``truncate_over`` tokens."""

    def __init__(self, truncate_over: int, context: int = 16384):
        self.truncate_over = truncate_over
        self.context = context
        self.calls: list[str] = []
        self.provider = "local"

    def ingestion_count_tokens(self, text: str) -> int:
        return len((text or "").split())

    def ingestion_context_tokens(self) -> int:
        return self.context

    def get_ingestion_model(self):
        return "stub"

    def _clean_json(self, raw: str) -> str:
        return raw

    @staticmethod
    def _note_text(prompt: str) -> str:
        # The note is the last thing in the prompt, after the final blank line.
        return prompt.rsplit("nothing else:\n\n", 1)[-1].strip()

    async def ingestion_generate_with_meta(self, prompt: str, temperature=0.1, max_tokens=None):
        note = self._note_text(prompt)
        self.calls.append(note)
        words = note.split()
        if len(words) > self.truncate_over:
            # Emulate a cut-off JSON body: unparsable AND flagged as truncated.
            return '{"nodes": [{"name": "half', {"finish_reason": "length", "truncated": True}
        nodes = [
            {"name": w, "type": "Word", "isolated_context": f"{w} appears in the note."}
            for w in dict.fromkeys(words)
        ]
        rels = [
            {"source_name": words[0], "target_name": words[-1], "relationship_type": "precedes"}
        ] if len(words) > 1 else []
        payload = {"title": f"Title for {words[0]}", "nodes": nodes, "relationships": rels}
        return json.dumps(payload), {"finish_reason": "stop", "truncated": False}

    async def ingestion_generate(self, prompt: str, temperature=0.0, max_tokens=None):
        content, _ = await self.ingestion_generate_with_meta(prompt, temperature)
        return content


@pytest.mark.asyncio
async def test_short_note_is_one_call():
    llm = _StubLLM(truncate_over=10_000)
    extraction, chunks = await agent._extract_with_chunking(llm, "alpha beta gamma", [])
    assert chunks == 1
    assert len(llm.calls) == 1
    assert {n.name for n in extraction.nodes} == {"alpha", "beta", "gamma"}


@pytest.mark.asyncio
async def test_long_note_is_chunked_on_paragraphs_and_merged(monkeypatch):
    monkeypatch.setenv("ORB_EXTRACTION_CHUNK_TOKENS", "400")
    paras = ["\n".join(f"p{p}w{w}" for w in range(50)) for p in range(20)]  # 1000 words
    note = "\n\n".join(paras)
    llm = _StubLLM(truncate_over=10_000)
    extraction, chunks = await agent._extract_with_chunking(llm, note, [])
    assert chunks >= 3
    assert len(llm.calls) == chunks
    assert len(extraction.nodes) == 1000  # every word survived, none duplicated
    assert extraction.title == "Title for p0w0"


@pytest.mark.asyncio
async def test_truncated_chunk_is_split_and_reextracted(monkeypatch):
    monkeypatch.setenv("ORB_EXTRACTION_CHUNK_TOKENS", "4000")
    # 600 words in ONE paragraph: fits the chunk budget, but the stub model
    # truncates anything over 450 words — the agent must halve and retry.
    note = " ".join(f"w{i}" for i in range(600))
    llm = _StubLLM(truncate_over=450)
    # Keep the test fast: no recovery sleeps on the truncation path expected,
    # but guard against a regression that turns truncation into a retry loop.
    slept: list[int] = []

    async def _no_sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(agent.asyncio, "sleep", _no_sleep)

    extraction, chunks = await agent._extract_with_chunking(llm, note, [])
    assert chunks == 1
    assert slept == []
    truncated_calls = [c for c in llm.calls if len(c.split()) > 450]
    assert len(truncated_calls) == 1
    assert len(extraction.nodes) == 600


@pytest.mark.asyncio
async def test_batch_image_titles_maps_tokens_and_falls_back():
    class _TitleLLM:
        async def ingestion_generate(self, prompt, temperature=0.0):
            return 'Sure! [{"index": 1, "title": "Company Logo"}, {"index": 2, "title": ""}, {"index": 9, "title": "x"}]'

    items = [
        {"token": "{{ORB_IMAGE_TITLE_0}}", "filename": "logo.png", "description": "a logo"},
        {"token": "{{ORB_IMAGE_TITLE_1}}", "filename": "cat.png", "description": "a cat"},
    ]
    titles = await agent._batch_image_titles(_TitleLLM(), items)
    assert titles == {"{{ORB_IMAGE_TITLE_0}}": "Company Logo"}

    class _BrokenLLM:
        async def ingestion_generate(self, prompt, temperature=0.0):
            raise RuntimeError("model down")

    assert await agent._batch_image_titles(_BrokenLLM(), items) == {}
