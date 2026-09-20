"""A retry replays the model calls that already succeeded and pays only for the rest."""

import asyncio

from app.services import ingestion_checkpoint as cp


class FakeLLM:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def get_ingestion_model(self):
        return "test-model"

    async def ingestion_generate_with_meta(self, prompt, temperature=0.1, **_kw):
        self.calls.append(prompt)
        if prompt == self.fail_on:
            raise RuntimeError("503 high demand")
        return f"out:{prompt}", {"truncated": False}


def test_retry_resumes_from_the_failed_call(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)

    async def run(llm):
        cp.activate("kb", "note")
        out = []
        for chunk in ("c1", "c2", "c3"):
            out.append(await cp.generate(llm, chunk))
        return out

    first = FakeLLM(fail_on="c3")
    try:
        asyncio.run(run(first))
    except RuntimeError:
        pass
    assert first.calls == ["c1", "c2", "c3"]

    second = FakeLLM()
    assert asyncio.run(run(second)) == ["out:c1", "out:c2", "out:c3"]
    assert second.calls == ["c3"]  # c1 and c2 replayed from disk

    cp.clear("kb", "note")
    third = FakeLLM()
    asyncio.run(run(third))
    assert third.calls == ["c1", "c2", "c3"]  # a fresh ingest after success re-runs all


def test_other_model_or_prompt_is_a_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)

    async def run():
        cp.activate("kb", "n")
        llm = FakeLLM()
        await cp.generate(llm, "p")
        await cp.generate(llm, "p", temperature=0.5)
        llm.get_ingestion_model = lambda: "other"
        await cp.generate(llm, "p")
        return llm.calls

    assert asyncio.run(run()) == ["p", "p", "p"]


def test_no_active_note_means_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)

    async def run():
        llm = FakeLLM()
        await cp.generate(llm, "p")
        await cp.generate(llm, "p")
        return llm.calls

    assert asyncio.run(run()) == ["p", "p"]
    assert not (tmp_path / "ingestion_cache").exists()
