"""A model unloaded between "is it loaded?" and "use it" must not be called as None.

Found in the research runs (orb-testing, rounds 4-5): the idle watcher or
another model's load could unload a model in that gap, and the call failed
with "'NoneType' object has no attribute ...".
"""

import threading
import time

from app.services.local_models import LocalGgufReranker, LocalLlamaRuntime


def test_reranker_reloads_when_unloaded_before_scoring(monkeypatch):
    reranker = LocalGgufReranker()
    loads = []

    def fake_ensure_loaded():
        # First time: another request unloads the model right after it was "loaded". Second time: it stays.
        loads.append(1)
        reranker._model = None if len(loads) == 1 else object()

    monkeypatch.setattr(reranker, "ensure_loaded", fake_ensure_loaded)
    monkeypatch.setattr(reranker, "_score_one", lambda query, doc: float(len(doc)))
    out = reranker.rerank("q", ["a", "bbb"])
    assert len(loads) == 2
    assert [r["document"] for r in out] == ["bbb", "a"]


def test_an_unload_cannot_slip_between_loading_and_generating(monkeypatch):
    """The round-4 failure: the idle watcher unloaded the chat model after the
    residency check and before generation began."""
    runtime = LocalLlamaRuntime()
    in_gap, seen = threading.Event(), {}

    def fake_ensure(chat_gguf=None):
        runtime._chat = object()
        in_gap.set()
        time.sleep(0.3)  # the gap: an unload fires now

    def fake_once(**kw):
        seen["chat_at_generation"] = runtime._chat is not None
        return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(runtime, "ensure_chat_loaded", fake_ensure)
    monkeypatch.setattr(runtime, "resolve_chat_gguf", lambda model=None: None)
    monkeypatch.setattr(runtime, "_remaining_output_budget", lambda messages: 64)
    monkeypatch.setattr(runtime, "_chat_completion_once", fake_once)
    monkeypatch.setattr(runtime, "_unload_unlocked", lambda release: setattr(runtime, "_chat", None))

    def unload_in_the_gap():
        in_gap.wait(2)
        runtime.unload()

    t = threading.Thread(target=unload_in_the_gap)
    t.start()
    try:
        runtime.create_chat_completion([{"role": "user", "content": "hi"}], max_tokens=64)
    except AssertionError:
        seen["chat_at_generation"] = False  # the old code asserted the model was still there
    t.join(5)
    assert seen["chat_at_generation"] is True
