"""Unit tests for transcription engine selection.

Rules proven in local-transcription-service: an explicit engine is never
substituted, the platform picks the layout, a model on disk in that layout is
used, and only an *installed* engine is ever chosen automatically.
"""

import pytest

from app.services import asr_engine as ae
from app.services.asr_engine import ENGINE_MLX, ENGINE_TRANSFORMERS

CONFIG = '{"model_type": "qwen3_asr", "architectures": ["Qwen3ASRForConditionalGeneration"]}'


def bundle(root, name):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(CONFIG)
    (d / "model.safetensors").write_bytes(b"\0" * 64)
    return d


@pytest.fixture()
def apple_all(monkeypatch):
    monkeypatch.setattr(ae, "engine_available", lambda e: True)
    monkeypatch.setattr(ae, "is_apple_silicon", lambda: True)


@pytest.fixture()
def linux_transformers(monkeypatch):
    monkeypatch.setattr(ae, "engine_available", lambda e: e == ENGINE_TRANSFORMERS)
    monkeypatch.setattr(ae, "is_apple_silicon", lambda: False)


class TestDefaults:
    def test_both_layouts_are_qwen3_asr(self):
        assert ae.DEFAULT_REPO[ENGINE_MLX] == "Qwen/Qwen3-ASR-1.7B"
        assert ae.DEFAULT_REPO[ENGINE_TRANSFORMERS] == "Qwen/Qwen3-ASR-1.7B-hf"
        assert ae.DEFAULT_LOCAL_DIR[ENGINE_MLX] == "qwen3-asr-1.7b"
        assert ae.DEFAULT_LOCAL_DIR[ENGINE_TRANSFORMERS] == "qwen3-asr-1.7b-hf"

    def test_language_names(self):
        assert ae.language_name("en") == "English"
        assert ae.language_name("xx") == "xx"
        assert ae.language_name(None) is None


class TestFormatDetection:
    def test_original_layout_is_mlx(self, tmp_path):
        assert ae.detect_engine_for(bundle(tmp_path, "qwen3-asr-1.7b")) == ENGINE_MLX

    def test_hf_layout_is_transformers(self, tmp_path):
        assert ae.detect_engine_for(bundle(tmp_path, "qwen3-asr-1.7b-hf")) == ENGINE_TRANSFORMERS

    def test_unrelated_folder_is_neither(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert ae.detect_engine_for(tmp_path / "empty") is None

    def test_whisper_folder_is_not_a_transcriber(self, tmp_path):
        d = tmp_path / "whisper-large-v3"
        d.mkdir()
        (d / "config.json").write_text('{"model_type": "whisper"}')
        (d / "model.safetensors").write_bytes(b"\0")
        assert ae.detect_engine_for(d) is None


class TestAutoSelection:
    def test_apple_uses_mlx_model_on_disk(self, tmp_path, apple_all):
        bundle(tmp_path, "qwen3-asr-1.7b")
        choice = ae.choose(tmp_path)
        assert choice.engine == ENGINE_MLX
        assert choice.ready is True

    def test_apple_with_nothing_on_disk_downloads_mlx_layout(self, tmp_path, apple_all):
        choice = ae.choose(tmp_path)
        assert choice.engine == ENGINE_MLX
        assert choice.ready is False
        assert choice.repo_id == "Qwen/Qwen3-ASR-1.7B"

    def test_apple_ignores_hf_layout_when_mlx_available(self, tmp_path, apple_all):
        bundle(tmp_path, "qwen3-asr-1.7b-hf")
        choice = ae.choose(tmp_path)
        assert choice.engine == ENGINE_MLX
        assert choice.ready is False

    def test_apple_without_mlx_package_falls_back_to_transformers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ae, "engine_available", lambda e: e == ENGINE_TRANSFORMERS)
        monkeypatch.setattr(ae, "is_apple_silicon", lambda: True)
        bundle(tmp_path, "qwen3-asr-1.7b-hf")
        choice = ae.choose(tmp_path)
        assert choice.engine == ENGINE_TRANSFORMERS
        assert choice.ready is True

    def test_other_platforms_use_transformers(self, tmp_path, linux_transformers):
        choice = ae.choose(tmp_path)
        assert choice.engine == ENGINE_TRANSFORMERS
        assert choice.repo_id == "Qwen/Qwen3-ASR-1.7B-hf"

    def test_smaller_model_on_disk_is_used(self, tmp_path, apple_all):
        bundle(tmp_path, "qwen3-asr-0.6b")
        assert ae.choose(tmp_path).model_path.name == "qwen3-asr-0.6b"


class TestExplicitEngine:
    def test_explicit_engine_is_never_substituted(self, tmp_path, linux_transformers):
        with pytest.raises(RuntimeError, match="Apple Silicon only"):
            ae.choose(tmp_path, preferred_engine=ENGINE_MLX)

    def test_explicit_mlx_missing_package_names_the_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ae, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(ae, "engine_available", lambda e: False)
        with pytest.raises(RuntimeError, match="pip install 'mlx-qwen3-asr"):
            ae.choose(tmp_path, preferred_engine=ENGINE_MLX)

    def test_unknown_engine_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown transcription engine"):
            ae.choose(tmp_path, preferred_engine="whisper")

    def test_explicit_transformers_finds_its_own_layout(self, tmp_path, apple_all):
        bundle(tmp_path, "qwen3-asr-1.7b")
        bundle(tmp_path, "qwen3-asr-1.7b-hf")
        choice = ae.choose(tmp_path, preferred_engine=ENGINE_TRANSFORMERS)
        assert choice.model_path.name == "qwen3-asr-1.7b-hf"


class TestExplicitPath:
    def test_explicit_path_uses_its_layout(self, tmp_path, apple_all):
        d = bundle(tmp_path, "qwen3-asr-1.7b")
        choice = ae.choose(tmp_path, explicit_path=d)
        assert choice.engine == ENGINE_MLX
        assert choice.reason == "explicit model path"

    def test_non_model_path_is_rejected(self, tmp_path, apple_all):
        (tmp_path / "nope").mkdir()
        with pytest.raises(ValueError, match="not a Qwen3-ASR model folder"):
            ae.choose(tmp_path, explicit_path=tmp_path / "nope")


class TestNoEngineInstalled:
    def test_names_the_engine_this_machine_should_use(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ae, "engine_available", lambda e: False)
        monkeypatch.setattr(ae, "is_apple_silicon", lambda: True)
        choice = ae.choose(tmp_path)
        assert choice.engine == ENGINE_MLX
        assert choice.reason == "no engine installed"


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

    def test_labels_number_speakers_by_first_appearance(self):
        out = ae.label_speakers(self._transcript(), self._turns())
        assert out == "Speaker 1: Good morning.\n\nSpeaker 2: Thanks, professor.\n\nSpeaker 1: Let's begin."

    def test_word_in_a_gap_goes_to_the_nearest_turn(self):
        assert ae.speaker_at(self._turns(), 1.5) == "SPEAKER_00"

    def test_without_timings_or_turns_text_is_unchanged(self):
        t = self._transcript()
        assert ae.label_speakers(ae.Transcript(t.text, []), self._turns()) == t.text
        assert ae.label_speakers(t, []) == t.text



class TestChunking:
    def test_short_audio_is_one_chunk(self):
        np = pytest.importorskip("numpy")
        audio = np.ones(16000 * 10, dtype="float32")
        assert len(ae.split_audio_into_chunks(audio, 16000)) == 1

    def test_long_audio_splits_in_the_quiet_part_with_offsets(self):
        np = pytest.importorskip("numpy")
        sr = 100
        audio = np.ones(sr * 50, dtype="float32")
        audio[sr * 24 : sr * 26] = 0.0  # a two-second pause near the middle
        chunks = ae.split_audio_into_chunks(audio, sr, max_chunk_sec=30)
        assert len(chunks) == 2 and chunks[0][1] == 0.0
        assert 24 <= chunks[1][1] <= 26  # the cut lands inside the pause
        assert sum(len(c) for c, _ in chunks) == len(audio)

    def test_aligner_layout_follows_the_engine(self):
        assert ae.ALIGNER_DIR[ENGINE_MLX] == "qwen3-forced-aligner-0.6b"
        assert ae.ALIGNER_DIR[ENGINE_TRANSFORMERS] == "qwen3-forced-aligner-0.6b-hf"


class TestRestorePunctuation:
    """The aligner drops punctuation; it must come back even when counts differ."""

    def _words(self, *bare):
        return [ae.Word(w, float(i), float(i) + 0.5) for i, w in enumerate(bare)]

    def test_extra_aligner_word_does_not_cost_the_punctuation(self):
        # "uh" was heard by the aligner but is not in the text: 7 words, 6 tokens.
        words = self._words("good", "morning", "uh", "thanks", "professor", "lets", "begin")
        ae.restore_punctuation(words, "Good morning. Thanks, professor. Let's begin!".split())
        assert [w.word for w in words] == ["Good", "morning.", "uh", "Thanks,", "professor.", "Let's", "begin!"]

    def test_missing_aligner_word_is_handled_too(self):
        words = self._words("good", "morning", "professor")
        ae.restore_punctuation(words, "Good morning, dear professor.".split())
        assert [w.word for w in words] == ["Good", "morning,", "professor."]

    def test_equal_lengths_still_work_and_timings_are_untouched(self):
        words = self._words("hello", "world")
        ae.restore_punctuation(words, "Hello, world.".split())
        assert [(w.word, w.start) for w in words] == [("Hello,", 0.0), ("world.", 1.0)]

    def test_labels_keep_their_full_stops_after_a_mismatch(self):
        words = self._words("good", "morning", "uh", "thanks", "professor")
        ae.restore_punctuation(words, "Good morning. Thanks, professor.".split())
        turns = [ae.Turn(0.0, 2.6, "A"), ae.Turn(2.9, 5.0, "B")]
        out = ae.label_speakers(ae.Transcript("Good morning. Thanks, professor.", words), turns)
        assert out == "Speaker 1: Good morning. uh\n\nSpeaker 2: Thanks, professor."


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
