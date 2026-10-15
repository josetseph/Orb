"""Ingestion on llama-server: parallel notes written in order, the build per platform, the grammar."""

import asyncio
import sys
import types
from pathlib import Path

from app.services import extraction_server as es
from app.workflows.ingestion import IngestionWorkflow


def _workflow(slots: int) -> IngestionWorkflow:
    wf = IngestionWorkflow.__new__(IngestionWorkflow)
    wf._concurrency = slots
    wf._process_semaphore = asyncio.Semaphore(slots)
    wf._next_ticket = wf._write_turn = 0
    wf._finished_tickets, wf._turn_events = set(), {}
    return wf


def test_writes_follow_submission_order():
    order: list[int] = []

    async def note(wf, ticket, extract_seconds, fail=False):
        async with wf._pipeline_slot(ticket) as write_turn:
            await asyncio.sleep(extract_seconds)  # extraction: the later notes finish first
            if fail:
                raise RuntimeError("unusable reply")  # never writes; must not hold the others back
            await write_turn()
            order.append(ticket)

    async def main():
        wf = _workflow(3)
        jobs = [note(wf, 0, 0.05), note(wf, 1, 0.01, fail=True), note(wf, 2, 0.0), note(wf, 3, 0.02)]
        await asyncio.gather(*jobs, return_exceptions=True)

    asyncio.run(main())
    assert order == [0, 2, 3]


def test_one_slot_runs_one_note_at_a_time():
    active, peak = 0, 0

    async def note(wf, ticket):
        nonlocal active, peak
        async with wf._pipeline_slot(ticket) as write_turn:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            await write_turn()
            active -= 1

    async def main():
        wf = _workflow(1)
        await asyncio.gather(*(note(wf, t) for t in range(3)))

    asyncio.run(main())
    assert peak == 1


def test_slots_grow_with_memory():
    assert [es.slots_for_ram(r) for r in (16, 24, 48, 128)] == [1, 2, 4, 6]


def test_build_per_platform(monkeypatch):
    def assets(plat, machine, nvidia):
        monkeypatch.setattr(es.sys, "platform", plat)
        monkeypatch.setattr(es.platform, "machine", lambda: machine)
        monkeypatch.setattr(es.shutil, "which", lambda name: "/bin/nvidia-smi" if nvidia else None)
        return es._assets()

    assert assets("darwin", "arm64", False) == [f"llama-{es.BUILD}-bin-macos-arm64.tar.gz"]
    assert assets("darwin", "x86_64", False) == [f"llama-{es.BUILD}-bin-macos-x64.tar.gz"]
    assert assets("win32", "AMD64", True)[0].endswith("win-cuda-12.4-x64.zip") and len(assets("win32", "AMD64", True)) == 2
    assert assets("win32", "AMD64", False) == [f"llama-{es.BUILD}-bin-win-vulkan-x64.zip"]
    assert assets("linux", "x86_64", True)[1].startswith("cudart-")
    assert assets("linux", "x86_64", False) == [f"llama-{es.BUILD}-bin-ubuntu-vulkan-x64.tar.gz"]


def test_json_mode_becomes_the_grammar(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, base_url, **kw):
            seen["base_url"] = base_url
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **kw: seen.update(kw)))

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(es, "_gguf_for", lambda model: Path("/m/gemma-4-12B.gguf"))
    monkeypatch.setattr(es.extraction_server, "url_for", lambda gguf: "http://127.0.0.1:9")
    es.ServerChatClient().chat.completions.create(
        model="gemma4-12b-q4", messages=[], response_format={"type": "json_object"}, temperature=0.1
    )
    assert "response_format" not in seen and "root   ::= object" in seen["extra_body"]["grammar"]
    assert seen["base_url"] == "http://127.0.0.1:9/v1" and seen["model"] == "gemma-4-12B.gguf"
    assert es.extraction_server._in_flight == 0


def test_extraction_model_choice(monkeypatch):
    from app.core.config import settings
    from app.services import model_catalog
    from app.services.llm import LLMService

    svc = LLMService.__new__(LLMService)
    svc.provider = svc.ingestion_provider = "local"
    svc._ingestion_model_override = None
    monkeypatch.setattr(svc, "get_chat_model", lambda: "qwen35-9b-q4")
    monkeypatch.setattr(model_catalog, "auto_extraction_model", lambda: "gemma4-12b-q4")

    monkeypatch.setattr(settings, "EXTRACTION_MODEL", "")
    assert svc.get_ingestion_model() == "gemma4-12b-q4"
    monkeypatch.setattr(settings, "EXTRACTION_MODEL", "chat")
    assert svc.get_ingestion_model() == "qwen35-9b-q4"
    monkeypatch.setattr(model_catalog, "auto_extraction_model", lambda: None)
    monkeypatch.setattr(settings, "EXTRACTION_MODEL", "")
    assert svc.get_ingestion_model() == "qwen35-9b-q4"  # the 12B does not fit or is not downloaded
    svc.ingestion_provider = "openai_compat"
    monkeypatch.setattr(settings, "EXTRACTION_MODEL", "gemma4-12b-q4")
    assert svc.get_ingestion_model() == "qwen35-9b-q4"  # a cloud endpoint ingests with its own model
