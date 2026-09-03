"""Finance chat synthesis must use the KB's own LLM, off the event loop.

Retrieval for a finance question already goes through the per-KB chat workflow;
this covers the final synthesis in ``FireflyService.answer_finance_question``,
which used to reach for the global ``llm_service`` singleton.
"""

import asyncio
import threading

import pytest

from app.services.firefly_service import FireflyService


class _RecordingLLM:
    """Stands in for a KB-pinned LLMService, recording how it was called."""

    def __init__(self, answer: str = "You spent $42."):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []
        self.thread_ids: list[int] = []

    def generate_text(self, system_prompt: str, user_prompt: str, model=None) -> str:
        self.calls.append((system_prompt, user_prompt))
        self.thread_ids.append(threading.get_ident())
        return self.answer


class _FakeKB:
    def __init__(self, llm, name: str = "Personal", kb_id: str = "kb-1"):
        self.llm = llm
        self.name = name
        self.kb_id = kb_id


@pytest.fixture()
def service(monkeypatch) -> FireflyService:
    svc = FireflyService.__new__(FireflyService)

    async def _workspace(_kb):
        return {"ready": True, "currency": "USD"}

    async def _summary(_kb, days=30):
        return {"days": days, "expense_total": 42.0}

    monkeypatch.setattr(svc, "get_workspace", _workspace, raising=False)
    monkeypatch.setattr(svc, "summary", _summary, raising=False)
    return svc


@pytest.mark.asyncio
async def test_uses_the_kb_llm_not_the_global_one(service, monkeypatch):
    import app.services.firefly_service as fs

    # Any use of the module-level global would be a regression.
    monkeypatch.setattr(fs, "llm_service", None, raising=False)
    llm = _RecordingLLM()
    result = await service.answer_finance_question("what did I spend?", _FakeKB(llm))

    assert result["answer"] == "You spent $42."
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_two_kbs_each_use_their_own_model(service):
    work, personal = _RecordingLLM("work answer"), _RecordingLLM("personal answer")
    r1 = await service.answer_finance_question("balance?", _FakeKB(work, name="Work"))
    r2 = await service.answer_finance_question("balance?", _FakeKB(personal, name="Personal"))

    assert (r1["answer"], r2["answer"]) == ("work answer", "personal answer")
    assert len(work.calls) == 1 and len(personal.calls) == 1


@pytest.mark.asyncio
async def test_synthesis_runs_off_the_event_loop(service):
    """A pinned KB can swap a multi-GB GGUF here; inline it would freeze /chat/status."""
    llm = _RecordingLLM()
    await service.answer_finance_question("spending?", _FakeKB(llm))
    assert llm.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_synthesis(service):
    """Concurrent tasks (status polls) keep running while the LLM blocks."""
    ticks = 0

    class _SlowLLM(_RecordingLLM):
        def generate_text(self, system_prompt, user_prompt, model=None):
            super().generate_text(system_prompt, user_prompt, model)
            threading.Event().wait(0.25)  # blocking, like a real GGUF call
            return self.answer

    async def _poll():
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.02)
            ticks += 1

    poller = asyncio.create_task(_poll())
    await service.answer_finance_question("spending?", _FakeKB(_SlowLLM()))
    await poller
    assert ticks == 10


@pytest.mark.asyncio
async def test_prompt_carries_kb_name_and_note_passages(service):
    llm = _RecordingLLM()
    docs = [{"original_obj": {"name": "Rent", "summary": "Rent is $1200/mo."}}]
    await service.answer_finance_question(
        "rent?", _FakeKB(llm, name="Personal"), note_docs=docs, rewritten_query="monthly rent"
    )
    _system, user = llm.calls[0]
    assert "Personal" in user
    assert "Rent is $1200/mo." in user
    assert "monthly rent" in user


@pytest.mark.asyncio
async def test_no_llm_call_when_firefly_is_not_ready(service, monkeypatch):
    async def _not_ready(_kb):
        return {"ready": False, "detail": "Firefly is still migrating."}

    monkeypatch.setattr(service, "get_workspace", _not_ready, raising=False)
    llm = _RecordingLLM()
    result = await service.answer_finance_question("balance?", _FakeKB(llm))

    assert llm.calls == []
    assert "Firefly is still migrating." in result["answer"]
