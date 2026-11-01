"""Ingestion is a durable job: it survives a restart, resumes, and gives up on a crash loop."""

import asyncio
import uuid

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, init_db
from app.models.job import Job
from app.models.note import Note
from app.services import jobs


class _Workflow:
    def __init__(self, calls):
        self.calls = calls

    async def process_note(self, payload, note_id, resumed=False):
        self.calls.append((note_id, payload.content))
        return {"status": "success"}


class _KB:
    vault_path = None

    def __init__(self, calls):
        self.wf = _Workflow(calls)

    def get_ingestion_workflow(self):
        return self.wf


def _setup(monkeypatch):
    from app.services import note_files
    from app.services.kb_registry import kb_registry

    calls: list = []
    kb_id = f"kb-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(kb_registry, "get_kb", lambda _id: _KB(calls))
    monkeypatch.setattr(note_files, "note_body", lambda note, kb: f"body of {note.id}")
    return kb_id, calls


async def _fresh() -> None:
    """The worker drains every queued job, so start from an empty table."""
    await init_db()
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Job))
        await s.commit()


async def _note(kb_id: str, stage: str = "Queued for ingestion") -> str:
    note_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as s:
        s.add(Note(id=note_id, kb_id=kb_id, processing_stage=stage))
        await s.commit()
    return note_id


async def _jobs(kb_id: str) -> list[Job]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(Job).where(Job.kb_id == kb_id).order_by(Job.id))).scalars())


async def _settle(kb_id: str) -> list[Job]:
    for _ in range(200):
        rows = await _jobs(kb_id)
        if all(j.state not in jobs.ACTIVE for j in rows):
            return rows
        await asyncio.sleep(0.01)
    raise AssertionError("jobs did not finish")


def test_a_queued_note_runs_and_one_left_running_by_a_crash_resumes(monkeypatch):
    kb_id, calls = _setup(monkeypatch)

    async def main():
        await _fresh()
        a, b = await _note(kb_id), await _note(kb_id, "Extracting entities")
        async with AsyncSessionLocal() as s:
            await jobs.enqueue_ingest(s, kb_id, a, "x")
            # b was running when the app died
            s.add(Job(kind=jobs.INGEST, kb_id=kb_id, target_id=b, input_hash="h", state="running", attempts=1))
            await s.commit()
        assert await jobs.recover() == []
        jobs.start()
        rows = await _settle(kb_id)
        assert [(j.target_id, j.state, j.attempts) for j in rows] == [(a, "done", 1), (b, "done", 2)]
        assert calls == [(a, f"body of {a}"), (b, f"body of {b}")]  # oldest first

    asyncio.run(main())


def test_a_job_interrupted_too_often_fails_instead_of_looping(monkeypatch):
    kb_id, calls = _setup(monkeypatch)

    async def main():
        await _fresh()
        n = await _note(kb_id, "Extracting entities")
        waiting = await _note(kb_id)  # still queued behind the others: it never ran
        async with AsyncSessionLocal() as s:
            for target in (n, waiting):
                s.add(Job(kind=jobs.INGEST, kb_id=kb_id, target_id=target, input_hash="h", state="running",
                          attempts=jobs.MAX_ATTEMPTS))
            await s.commit()
        assert await jobs.recover() == [n]
        jobs.start()
        rows = await _settle(kb_id)
        assert rows[0].state == "failed" and "interrupted" in rows[0].last_error
        assert rows[1].state == "done"
        assert [c[0] for c in calls] == [waiting]

    asyncio.run(main())


def test_same_input_is_one_job_new_input_supersedes_and_cancel_stops_a_queued_one(monkeypatch):
    kb_id, calls = _setup(monkeypatch)

    async def main():
        await _fresh()
        n = await _note(kb_id)
        async with AsyncSessionLocal() as s:
            await jobs.enqueue_ingest(s, kb_id, n, "v1")
            await s.commit()
            await jobs.enqueue_ingest(s, kb_id, n, "v1")
            await s.commit()
            assert len(await _jobs(kb_id)) == 1
            await jobs.enqueue_ingest(s, kb_id, n, "v2")
            await s.commit()
        assert [j.state for j in await _jobs(kb_id)] == ["cancelled", "queued"]
        assert await jobs.cancel(kb_id, n) is True
        assert await jobs.cancel(kb_id, n) is False
        jobs.start()
        await asyncio.sleep(0.05)
        assert [j.state for j in await _jobs(kb_id)] == ["cancelled", "cancelled"]
        assert calls == []

    asyncio.run(main())


def test_an_answer_cut_off_by_a_restart_is_resumed_and_saved(monkeypatch):
    from app.api import chat
    from app.services.chat_store import chat_store
    from app.services.kb_registry import kb_registry

    kb_id = f"kb-{uuid.uuid4().hex[:8]}"
    stages: list[str] = []

    class KB:
        def __init__(self):
            self.kb_id = kb_id
            self.llm = type("L", (), {"summarize_conversation": staticmethod(lambda *a, **k: "")})()

    async def answer(query, kb, history, progress):
        stages.append(chat._get_status(request_id, {}).get("stage"))
        return {"answer": f"answer to {query}", "context": [], "sources": []}

    monkeypatch.setattr(kb_registry, "get_kb", lambda _id: KB())
    monkeypatch.setattr(chat, "_answer_chat_query", answer)
    request_id = str(uuid.uuid4())

    async def main():
        await _fresh()
        conv = await chat_store.create_conversation(kb_id)
        await chat_store.add_message(conv["id"], "user", "what is Orb?")
        async with AsyncSessionLocal() as s:  # the app died while answering
            s.add(Job(kind=jobs.ANSWER, kb_id=kb_id, target_id=request_id, input_hash="h", state="running",
                      attempts=1, payload={"conversation_id": conv["id"], "query": "what is Orb?", "history": []}))
            await s.commit()
        await jobs.recover()
        jobs.start()
        rows = await _settle(kb_id)
        assert [j.state for j in rows] == ["done"]
        messages = await chat_store.list_messages(conv["id"], kb_id=kb_id)
        assert [m["content"] for m in messages] == ["what is Orb?", "answer to what is Orb?"]

    asyncio.run(main())
    assert stages == ["Resuming answer"]


def test_an_attachment_job_runs_from_the_table_and_a_cancel_stops_it(monkeypatch):
    from app.api import notes as notes_api
    from app.services.kb_registry import kb_registry

    kb_id = f"kb-{uuid.uuid4().hex[:8]}"
    ran: list[str] = []

    async def extract(kb, note_id, url):
        ran.append(url)
        if url.endswith("long.m4a"):
            await asyncio.sleep(60)

    monkeypatch.setattr(kb_registry, "get_kb", lambda _id: object())
    monkeypatch.setattr(notes_api, "_run_attachment_job", extract)

    async def main():
        await _fresh()
        async with AsyncSessionLocal() as s:
            for url in ("/a/short.pdf", "/a/long.m4a"):
                await jobs.enqueue(s, jobs.ATTACHMENT, kb_id, notes_api._attachment_target("n1", url),
                                   jobs.input_hash(url), payload={"note_id": "n1", "url": url})
            await s.commit()
        jobs.start()
        for _ in range(200):
            if len(ran) == 2:
                break
            await asyncio.sleep(0.01)
        assert await jobs.cancel(kb_id, notes_api._attachment_target("n1", "/a/long.m4a"), kind=jobs.ATTACHMENT)
        rows = await _settle(kb_id)
        assert {j.payload["url"]: j.state for j in rows} == {"/a/short.pdf": "done", "/a/long.m4a": "cancelled"}

    asyncio.run(main())
