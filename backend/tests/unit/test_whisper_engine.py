"""Unit tests for Whisper engine selection.

Mirrors the rules proven in local-transcription-service: an explicit engine is
never substituted, a local model's format decides in auto mode, and only an
*installed* engine is ever chosen automatically.
"""

import pytest

from app.services import whisper_engine as we
from app.services.whisper_engine import ENGINE_MLX, ENGINE_TRANSFORMERS


def mlx_bundle(root, name="whisper-large-v3-mlx"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}")
    (d / "weights.npz").write_bytes(b"\0" * 64)
    return d


def hf_bundle(root, name="whisper-large-v3"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}")
    (d / "model.safetensors").write_bytes(b"\0" * 64)
    return d


@pytest.fixture()
def both_engines(monkeypatch):
    monkeypatch.setattr(we, "engine_available", lambda e: True)
    monkeypatch.setattr(we, "is_apple_silicon", lambda: True)


class TestDefaults:
    def test_default_is_large_v3_not_turbo(self):
        """turbo's shallow decoder hallucinates on noisy audio."""
        assert "turbo" not in we.DEFAULT_REPO[ENGINE_MLX]
        assert "turbo" not in we.DEFAULT_REPO[ENGINE_TRANSFORMERS]
        assert we.DEFAULT_REPO[ENGINE_MLX] == "mlx-community/whisper-large-v3-mlx"
        assert we.DEFAULT_REPO[ENGINE_TRANSFORMERS] == "openai/whisper-large-v3"


class TestFormatDetection:
    def test_npz_weights_are_mlx(self, tmp_path):
        assert we.detect_engine_for(mlx_bundle(tmp_path)) == ENGINE_MLX

    def test_safetensors_are_transformers(self, tmp_path):
        assert we.detect_engine_for(hf_bundle(tmp_path)) == ENGINE_TRANSFORMERS

    def test_mlx_named_safetensors_folder_is_mlx(self, tmp_path):
        d = hf_bundle(tmp_path, "whisper-large-v3-mlx")
        assert we.detect_engine_for(d) == ENGINE_MLX

    def test_unrelated_folder_is_neither(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert we.detect_engine_for(tmp_path / "empty") is None


class TestAutoSelection:
    def test_apple_silicon_prefers_mlx_when_nothing_on_disk(self, tmp_path, both_engines):
        choice = we.choose(tmp_path)
        assert choice.engine == ENGINE_MLX
        assert choice.repo_id == we.DEFAULT_REPO[ENGINE_MLX]
        assert choice.ready is False

    def test_other_platforms_prefer_transformers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(we, "engine_available", lambda e: e == ENGINE_TRANSFORMERS)
        monkeypatch.setattr(we, "is_apple_silicon", lambda: False)
        assert we.choose(tmp_path).engine == ENGINE_TRANSFORMERS

    def test_a_model_on_disk_decides_the_engine(self, tmp_path, both_engines):
        hf_bundle(tmp_path)
        choice = we.choose(tmp_path)
        assert choice.engine == ENGINE_TRANSFORMERS
        assert choice.ready is True

    def test_an_existing_turbo_install_keeps_working(self, tmp_path, both_engines):
        """Changing the default must not break someone who already has turbo."""
        hf_bundle(tmp_path, "whisper-large-v3-turbo")
        choice = we.choose(tmp_path)
        assert choice.ready is True
        assert choice.model_path.name == "whisper-large-v3-turbo"

    def test_large_v3_wins_over_turbo_when_both_present(self, tmp_path, both_engines):
        hf_bundle(tmp_path, "whisper-large-v3-turbo")
        hf_bundle(tmp_path, "whisper-large-v3")
        assert we.choose(tmp_path).model_path.name == "whisper-large-v3"

    def test_mlx_bundle_ignored_when_mlx_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(we, "engine_available", lambda e: e == ENGINE_TRANSFORMERS)
        monkeypatch.setattr(we, "is_apple_silicon", lambda: False)
        mlx_bundle(tmp_path)
        choice = we.choose(tmp_path)
        assert choice.engine == ENGINE_TRANSFORMERS
        assert choice.ready is False  # the MLX folder is unusable here


class TestExplicitEngine:
    def test_explicit_engine_is_never_substituted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(we, "engine_available", lambda e: e == ENGINE_TRANSFORMERS)
        monkeypatch.setattr(we, "is_apple_silicon", lambda: False)
        with pytest.raises(RuntimeError, match="Apple Silicon only"):
            we.choose(tmp_path, preferred_engine=ENGINE_MLX)

    def test_explicit_mlx_missing_package_names_the_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr(we, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(we, "engine_available", lambda e: False)
        with pytest.raises(RuntimeError, match="pip install 'mlx-whisper"):
            we.choose(tmp_path, preferred_engine=ENGINE_MLX)

    def test_unknown_engine_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown Whisper engine"):
            we.choose(tmp_path, preferred_engine="faster-whisper")

    def test_explicit_engine_finds_its_own_model(self, tmp_path, both_engines):
        mlx_bundle(tmp_path)
        hf_bundle(tmp_path)
        choice = we.choose(tmp_path, preferred_engine=ENGINE_MLX)
        assert choice.engine == ENGINE_MLX
        assert choice.model_path.name.endswith("-mlx")


class TestExplicitPath:
    def test_explicit_path_uses_its_format(self, tmp_path, both_engines):
        d = mlx_bundle(tmp_path)
        choice = we.choose(tmp_path, explicit_path=d)
        assert choice.engine == ENGINE_MLX
        assert choice.reason == "explicit model path"

    def test_non_model_path_is_rejected(self, tmp_path, both_engines):
        (tmp_path / "nope").mkdir()
        with pytest.raises(ValueError, match="not a Whisper model folder"):
            we.choose(tmp_path, explicit_path=tmp_path / "nope")


class TestNoEngineInstalled:
    def test_names_the_engine_this_machine_should_use(self, tmp_path, monkeypatch):
        monkeypatch.setattr(we, "engine_available", lambda e: False)
        monkeypatch.setattr(we, "is_apple_silicon", lambda: True)
        choice = we.choose(tmp_path)
        assert choice.engine == ENGINE_MLX
        assert choice.reason == "no engine installed"
