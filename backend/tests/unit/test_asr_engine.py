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
