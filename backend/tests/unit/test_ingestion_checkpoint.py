"""A retry replays the steps that already succeeded and pays only for the rest."""

import sys
import types

import numpy as np

from app.services import asr_engine
from app.services import ingestion_checkpoint as cp
from app.services.llm import LLMService


def _llm(fail_on=None):
    """An LLMService whose provider call is a counter (``_chat`` and its memo are real)."""
    svc = LLMService.__new__(LLMService)
    svc.provider = svc.ingestion_provider = "local"
    svc.get_ingestion_model = lambda: "test-model"
    svc.get_chat_model = lambda: "test-model"
    svc.calls = []

    def complete(messages, **kw):
        prompt = messages[-1]["content"]
        svc.calls.append(prompt)
        if prompt == fail_on:
            raise RuntimeError("503 high demand")
        return ("" if prompt == "empty" else f"out:{prompt}"), {"truncated": False}

    svc._complete = complete
    return svc


def _run(llm, prompts, temperature=0.1):
    return [llm._chat([{"role": "user", "content": p}], temperature=temperature, ingestion=True)[0]
            for p in prompts]


def test_retry_resumes_from_the_failed_call(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)
    cp.activate("kb", "note")

    first = _llm(fail_on="c3")
    try:
        _run(first, ["c1", "c2", "c3"])
    except RuntimeError:
        pass
    second = _llm()
    assert _run(second, ["c1", "c2", "c3"]) == ["out:c1", "out:c2", "out:c3"]
    assert second.calls == ["c3"]  # c1 and c2 replayed from disk

    cp.clear("kb", "note")
    third = _llm()
    _run(third, ["c1", "c2", "c3"])
    assert third.calls == ["c1", "c2", "c3"]  # a fresh ingest after success re-runs all


def test_other_model_settings_or_prompt_is_a_miss_and_failures_are_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)
    cp.activate("kb", "n")
    llm = _llm()
    _run(llm, ["p"])
    _run(llm, ["p"], temperature=0.5)
    llm.get_ingestion_model = lambda: "other"
    _run(llm, ["p", "empty", "empty"])
    assert llm.calls == ["p", "p", "p", "empty", "empty"]


def test_forget_makes_the_retry_ask_again(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)
    cp.activate("kb", "n")
    llm = _llm()
    ask = lambda: llm._chat([{"role": "user", "content": "p"}], temperature=0.1, ingestion=True, json_mode=True)
    ask()
    ask()
    cp.forget_reply(llm, "p", temperature=0.1)
    ask()
    assert llm.calls == ["p", "p"]


def test_no_active_note_means_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)
    cp._active.set(None)
    llm = _llm()
    _run(llm, ["p", "p"])
    assert llm.calls == ["p", "p"]
    assert not (tmp_path / "ingestion_cache").exists()


def test_a_transcription_resumes_at_the_first_undecoded_window(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "resolve_data_dir", lambda: tmp_path)
    windows = [np.full(4, i, dtype=np.float32) for i in range(3)]

    class Result:
        truncated, timed, words = False, False, []

        def __init__(self, text):
            self.text = text

    def transcribe_windows(audio, decode_one):
        return Result(" ".join(decode_one(w)[0] for w in windows))

    segment = types.SimpleNamespace(transcribe_windows=transcribe_windows)
    engine = types.SimpleNamespace(read_audio=lambda path: np.concatenate(windows))
    monkeypatch.setitem(sys.modules, "fermion", types.ModuleType("fermion"))
    monkeypatch.setitem(sys.modules, "fermion._speech", types.SimpleNamespace(segment=segment, engine=engine))
    monkeypatch.setitem(sys.modules, "fermion._speech.segment", segment)
    monkeypatch.setitem(sys.modules, "fermion._speech.engine", engine)

    class Speech:
        def __init__(self, crash_at=None):
            self.decoded, self.crash_at = [], crash_at

        def _decode_segment(self, audio):
            i = int(audio[0])
            if i == self.crash_at:
                raise RuntimeError("power cut")
            self.decoded.append(i)
            return f"w{i}", 1, 10

    cp.activate("kb", "rec")
    try:
        asr_engine.transcribe_with_phonon(Speech(crash_at=2), "a.wav")
    except RuntimeError:
        pass
    again = Speech()
    assert asr_engine.transcribe_with_phonon(again, "a.wav").text == "w0 w1 w2"
    assert again.decoded == [2]
