"""Unit tests for transcription: Phonon-2 through fermion, and speaker labels.

fermion-research is an on-demand media package (not in CI), so it is stood in
for with a fake module; the contract tested is the one Orb relies on.
"""

import sys
import types

import pytest

from app.services import asr_engine as ae


class FakeResult:
    def __init__(self, *, truncated=False, timed=True):
        self.text = "Good morning. Let's begin."
        self.words = [
            {"text": "Good", "start": 0.0, "end": 0.3},
            {"text": "morning.", "start": 0.3, "end": 0.6},
            {"text": "Let's", "start": 1.0, "end": 1.2},
            {"text": "begin.", "start": 1.2, "end": 1.6},
        ]
        self.timed = timed
        self.truncated = truncated


class FakeSpeech:
    def __init__(self, result=None, exit_message=None):
        self.result, self.exit_message, self.paths = result, exit_message, []

    def transcribe_detailed(self, path):
        self.paths.append(path)
        if self.exit_message:
            raise SystemExit(self.exit_message)
        return self.result


class TestPhonon:
    def test_words_map_onto_orbs_transcript(self):
        t = ae.transcribe_with_phonon(FakeSpeech(FakeResult()), "a.wav")
        assert t.text == "Good morning. Let's begin."
        assert [(w.word, w.start, w.end) for w in t.words][:2] == [("Good", 0.0, 0.3), ("morning.", 0.3, 0.6)]
        # Punctuated words straight from the decoder feed the line builder.
        assert ae.timed_lines(t, []).startswith("[00:00] Good morning.")

    def test_a_truncated_result_is_a_failure_not_a_short_transcript(self):
        with pytest.raises(RuntimeError, match="truncated"):
            ae.transcribe_with_phonon(FakeSpeech(FakeResult(truncated=True)), "a.wav")

    def test_untimed_result_keeps_the_text_without_words(self):
        t = ae.transcribe_with_phonon(FakeSpeech(FakeResult(timed=False)), "a.wav")
        assert t.words == [] and t.text

    def test_fermions_system_exit_becomes_a_runtime_error(self):
        # SystemExit would sail past `except Exception` and end the worker.
        with pytest.raises(RuntimeError, match="no such file"):
            ae.transcribe_with_phonon(FakeSpeech(exit_message="a.wav: no such file"), "a.wav")

    def test_load_refuses_without_downloading_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ae, "is_phonon_ready", lambda models_dir: False)
        called = {}
        fake = types.ModuleType("fermion")
        fake.load_speech = lambda *a, **k: called.setdefault("load", True)
        monkeypatch.setitem(sys.modules, "fermion", fake)
        with pytest.raises(RuntimeError, match="Models page"):
            ae.load_phonon(tmp_path)
        assert called == {}

    def test_load_points_fermion_at_orbs_models_folder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ae, "is_phonon_ready", lambda models_dir: True)
        fake = types.ModuleType("fermion")
        fake.load_speech = lambda model, **k: ("speech", model)
        monkeypatch.setitem(sys.modules, "fermion", fake)
        monkeypatch.delenv("FERMION_CACHE_DIR", raising=False)
        assert ae.load_phonon(tmp_path) == ("speech", "phonon-2")
        import os

        assert os.environ["FERMION_CACHE_DIR"] == str(tmp_path / "fermion")

    def test_load_system_exit_becomes_a_runtime_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ae, "is_phonon_ready", lambda models_dir: True)
        fake = types.ModuleType("fermion")

        def refuse(model, **k):
            raise SystemExit("speech is unavailable: no engine")

        fake.load_speech = refuse
        monkeypatch.setitem(sys.modules, "fermion", fake)
        with pytest.raises(RuntimeError, match="no engine"):
            ae.load_phonon(tmp_path)

    def test_ready_needs_both_model_files(self, monkeypatch, tmp_path):
        d = tmp_path / "profile"
        d.mkdir()
        monkeypatch.setattr(ae, "phonon_dir", lambda models_dir: d)
        assert not ae.is_phonon_ready(tmp_path)
        (d / "config.json").write_text("{}")
        assert not ae.is_phonon_ready(tmp_path)
        (d / "model.fermion").write_bytes(b"\0")
        assert ae.is_phonon_ready(tmp_path)

    def test_not_ready_when_fermion_is_not_installed(self, monkeypatch, tmp_path):
        def missing(models_dir):
            raise ModuleNotFoundError("No module named 'fermion'")

        monkeypatch.setattr(ae, "phonon_dir", missing)
        assert ae.is_phonon_ready(tmp_path) is False


class TestSpeakerLabels:
    def _transcript(self):
        words = [
            ae.Word("Good", 0.0, 0.3), ae.Word("morning.", 0.3, 0.6),
            ae.Word("Thanks,", 2.0, 2.3), ae.Word("professor.", 2.3, 2.8),
            ae.Word("Let's", 4.0, 4.2), ae.Word("begin.", 4.2, 4.6),
        ]
        return ae.Transcript("Good morning. Thanks, professor. Let's begin.", words)

    def _turns(self):
        return [ae.Turn(0.0, 1.0, "SPEAKER_01"), ae.Turn(1.9, 3.0, "SPEAKER_00"), ae.Turn(3.9, 5.0, "SPEAKER_01")]

    def test_lines_are_stamped_and_speakers_numbered_by_first_appearance(self):
        out = ae.timed_lines(self._transcript(), self._turns())
        assert out.splitlines()[0].startswith("[00:00] Speaker 1: Good morning.")
        assert "Speaker 2: Thanks, professor." in out and out.count("Speaker 1:") == 2
        # Each entry is its own Markdown paragraph.
        entries = out.split("\n\n")
        assert len(entries) == 3 and all(e.startswith("[00:0") and "\n" not in e for e in entries)

    def test_word_in_a_gap_goes_to_the_nearest_turn(self):
        assert ae.speaker_at(self._turns(), 1.5) == "SPEAKER_00"

    def test_without_turns_lines_are_still_timed_and_without_words_text_is_plain(self):
        t = self._transcript()
        assert ae.timed_lines(t, []).startswith("[00:00] Good morning.")
        assert ae.timed_lines(ae.Transcript(t.text, []), self._turns()) == t.text

    def test_untimed_gives_the_summariser_speaker_paragraphs(self):
        text = "[00:01] Speaker 1: Hello.\n[00:05] Speaker 1: Welcome.\n[01:10] Speaker 2: Hi."
        assert ae.untimed(text) == "Speaker 1: Hello. Welcome.\n\nSpeaker 2: Hi."
        assert ae.untimed("[00:01] Hello.\n[00:05] Welcome.") == "Hello.\n\nWelcome."
        # Blank-line separated (as written now) reads the same as the old form.
        assert ae.untimed(text.replace("\n", "\n\n")) == "Speaker 1: Hello. Welcome.\n\nSpeaker 2: Hi."



def test_diarizer_uses_the_accelerator_and_frees_it(monkeypatch):
    import sys
    import types

    import numpy as np

    seen = {}

    class Pipeline:
        _segmentation = types.SimpleNamespace(step=0.0)

        @classmethod
        def from_pretrained(cls, path):
            return cls()

        def to(self, device):
            seen["device"] = str(device)

        def __call__(self, inputs, **kw):
            seg = types.SimpleNamespace(start=0.0, end=1.0)
            return types.SimpleNamespace(itertracks=lambda yield_label: [(seg, None, "SPEAKER_00")])

    pyannote = types.ModuleType("pyannote")
    audio = types.ModuleType("pyannote.audio")
    audio.Pipeline = Pipeline
    monkeypatch.setitem(sys.modules, "pyannote", pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
    # torch is installed on demand, not in the test venv or CI.
    torch = types.ModuleType("torch")
    torch.device = lambda name: name
    torch.from_numpy = lambda a: types.SimpleNamespace(unsqueeze=lambda dim: a)
    monkeypatch.setitem(sys.modules, "torch", torch)
    device_mod = types.ModuleType("app.core.inference_device")
    device_mod.resolve_torch_device = lambda: "mps"
    monkeypatch.setitem(sys.modules, "app.core.inference_device", device_mod)
    from app.services import local_models

    monkeypatch.setattr(local_models, "release_accelerator_memory", lambda: seen.setdefault("freed", True))

    turns = ae.speaker_turns(np.zeros(16000, dtype="float32"), 16000, __import__("pathlib").Path("/x"), step=2.0, max_speakers=None)
    assert [t.speaker for t in turns] == ["SPEAKER_00"]
    assert seen == {"device": "mps", "freed": True}  # device comes from the shared resolver
