"""Unit tests for local model format detection and runtime resolution.

Fixtures are minimal on-disk layouts rather than real weights: detection reads
structure and config.json, never tensors.
"""

import json

import pytest

from app.services import model_formats as mf
from app.services.model_formats import ModelFormat, Runtime


def gguf_file(path, size=64 * 1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * size)
    return path


def hf_dir(path, *, config=None, weights="model.safetensors"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps(config or {"model_type": "llama"}))
    if weights:
        (path / weights).write_bytes(b"\0" * 2048)
    return path


class TestDetectFormat:
    def test_single_gguf_file(self, tmp_path):
        assert mf.detect_format(gguf_file(tmp_path / "m.gguf")) is ModelFormat.GGUF

    def test_folder_containing_a_gguf(self, tmp_path):
        gguf_file(tmp_path / "bundle" / "m.gguf")
        assert mf.detect_format(tmp_path / "bundle") is ModelFormat.GGUF

    def test_plain_hf_folder_is_transformers(self, tmp_path):
        assert mf.detect_format(hf_dir(tmp_path / "hf")) is ModelFormat.TRANSFORMERS

    def test_npz_weights_are_mlx(self, tmp_path):
        d = hf_dir(tmp_path / "mlx", weights=None)
        (d / "weights.npz").write_bytes(b"\0" * 2048)
        assert mf.detect_format(d) is ModelFormat.MLX

    def test_quantization_block_marks_mlx(self, tmp_path):
        """mlx_lm writes a quantization block when it converts a model."""
        d = hf_dir(tmp_path / "q", config={"model_type": "llama", "quantization": {"group_size": 64, "bits": 4}})
        assert mf.detect_format(d) is ModelFormat.MLX

    def test_gguf_wins_over_config_json(self, tmp_path):
        d = hf_dir(tmp_path / "both")
        gguf_file(d / "m.gguf")
        assert mf.detect_format(d) is ModelFormat.GGUF

    def test_unrecognised_paths(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert mf.detect_format(tmp_path / "empty") is None
        assert mf.detect_format(tmp_path / "missing") is None
        (tmp_path / "note.txt").write_text("hi")
        assert mf.detect_format(tmp_path / "note.txt") is None

    def test_config_without_weights_is_not_a_model(self, tmp_path):
        d = tmp_path / "cfgonly"
        d.mkdir()
        (d / "config.json").write_text("{}")
        assert mf.detect_format(d) is None

    def test_unreadable_config_does_not_crash(self, tmp_path):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "config.json").write_text("{not json")
        (d / "model.safetensors").write_bytes(b"\0" * 16)
        assert mf.detect_format(d) is ModelFormat.TRANSFORMERS


class TestShardsAndLoadablePath:
    def test_only_the_first_shard_is_loadable(self, tmp_path):
        d = tmp_path / "split"
        for i in (1, 2, 3):
            gguf_file(d / f"Big-{i:05d}-of-00003.gguf")
        assert mf.loadable_path(d, ModelFormat.GGUF).name == "Big-00001-of-00003.gguf"

    def test_shard_continuation_detected(self, tmp_path):
        assert mf.is_shard_continuation(tmp_path / "M-00002-of-00003.gguf") is True
        assert mf.is_shard_continuation(tmp_path / "M-00001-of-00003.gguf") is False
        assert mf.is_shard_continuation(tmp_path / "Plain.gguf") is False

    def test_file_path_passes_through(self, tmp_path):
        f = gguf_file(tmp_path / "m.gguf")
        assert mf.loadable_path(f, ModelFormat.GGUF) == f

    def test_hf_folder_stays_a_folder(self, tmp_path):
        d = hf_dir(tmp_path / "hf")
        assert mf.loadable_path(d, ModelFormat.TRANSFORMERS) == d


class TestResolveRuntime:
    def test_gguf_maps_to_llama_cpp(self):
        runtime, reason = mf.resolve_runtime(ModelFormat.GGUF)
        assert runtime is Runtime.LLAMA_CPP
        assert reason is None  # llama_cpp ships with the backend

    def test_mlx_is_refused_off_apple_silicon(self, monkeypatch):
        monkeypatch.setattr(mf, "is_apple_silicon", lambda: False)
        runtime, reason = mf.resolve_runtime(ModelFormat.MLX)
        assert runtime is Runtime.MLX_LM
        assert "Apple Silicon only" in reason

    def test_missing_runtime_names_the_install(self, monkeypatch):
        monkeypatch.setattr(mf, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(mf, "runtime_installed", lambda r: False)
        _runtime, reason = mf.resolve_runtime(ModelFormat.MLX)
        assert "mlx_lm" in reason and "pip install mlx-lm" in reason

    def test_transformers_missing_points_at_the_extras(self, monkeypatch):
        monkeypatch.setattr(mf, "runtime_installed", lambda r: False)
        _runtime, reason = mf.resolve_runtime(ModelFormat.TRANSFORMERS)
        assert "requirements-multimodal.txt" in reason


class TestDescribe:
    def test_gguf_description(self, tmp_path):
        model = mf.describe(gguf_file(tmp_path / "My-Model-Q4_K_M.gguf"))
        assert model.format is ModelFormat.GGUF
        assert model.name == "My-Model-Q4_K_M"
        assert model.runtime is Runtime.LLAMA_CPP
        assert model.runnable is True

    def test_split_gguf_name_drops_the_shard_suffix(self, tmp_path):
        d = tmp_path / "split"
        for i in (1, 2):
            gguf_file(d / f"Big-Model-{i:05d}-of-00002.gguf")
        assert mf.describe(d).name == "Big-Model"

    def test_hf_name_comes_from_config(self, tmp_path):
        d = hf_dir(tmp_path / "x", config={"_name_or_path": "org/Some-Chat-7B"})
        assert mf.describe(d).name == "Some-Chat-7B"

    def test_unsupported_layout_is_described_not_hidden(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mf, "is_apple_silicon", lambda: False)
        d = hf_dir(tmp_path / "mlx", config={"quantization": {"bits": 4}})
        model = mf.describe(d)
        assert model.format is ModelFormat.MLX
        assert model.runnable is False
        assert "Apple Silicon" in model.unsupported_reason

    def test_reranker_and_embedder_names_warn(self, tmp_path):
        assert any("reranker" in w.lower() for w in mf.describe(gguf_file(tmp_path / "Qwen3-Reranker-4B.gguf")).warnings)
        assert any("embedding" in w.lower() for w in mf.describe(gguf_file(tmp_path / "Qwen3-Embedding-4B.gguf")).warnings)

    def test_non_model_returns_none(self, tmp_path):
        (tmp_path / "readme.md").write_text("x")
        assert mf.describe(tmp_path / "readme.md") is None

    def test_size_is_reported(self, tmp_path):
        model = mf.describe(gguf_file(tmp_path / "m.gguf", size=3 * 1024 * 1024))
        assert model.size_bytes > 3_000_000
        assert model.size_gb >= 0


class TestRejectsNonChatModels:
    """Orb's own models directory holds Whisper and Florence in HF layout."""

    def test_whisper_folder_is_not_offered_for_chat(self, tmp_path):
        d = hf_dir(
            tmp_path / "whisper",
            config={"model_type": "whisper", "architectures": ["WhisperForConditionalGeneration"]},
        )
        model = mf.describe(d)
        assert model.runnable is False
        assert "speech-recognition" in model.unsupported_reason

    def test_vision_folder_is_not_offered_for_chat(self, tmp_path):
        d = hf_dir(tmp_path / "florence2", config={"model_type": "florence2"})
        assert "vision model" in mf.describe(d).unsupported_reason

    def test_non_causal_architecture_is_rejected_by_name(self, tmp_path):
        d = hf_dir(
            tmp_path / "enc",
            config={"model_type": "something", "architectures": ["XLMRobertaModel"]},
        )
        assert "cannot generate chat" in mf.describe(d).unsupported_reason

    def test_causal_lm_is_accepted(self, tmp_path):
        d = hf_dir(
            tmp_path / "chat",
            config={"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]},
        )
        assert mf.describe(d).runnable is True

    def test_config_without_architectures_is_allowed(self, tmp_path):
        """Absent metadata should not block a model that may well work."""
        assert mf.describe(hf_dir(tmp_path / "bare")).runnable is True


class TestGgufMagic:
    def test_appledouble_sidecar_is_not_a_model(self, tmp_path):
        """macOS writes 4 KB ._name files beside models on exFAT volumes."""
        sidecar = tmp_path / "._Real-Model.gguf"
        sidecar.write_bytes(b"\x00\x05\x16\x07" + b"\0" * 4092)
        assert mf.detect_format(sidecar) is None
        assert mf.describe(sidecar) is None

    def test_extension_alone_is_not_enough(self, tmp_path):
        fake = tmp_path / "fake.gguf"
        fake.write_bytes(b"not a model" * 100)
        assert mf.detect_format(fake) is None

    def test_sidecars_are_skipped_when_scanning_a_folder(self, tmp_path):
        d = tmp_path / "models"
        gguf_file(d / "Real.gguf")
        (d / "._Real.gguf").write_bytes(b"\x00\x05\x16\x07" + b"\0" * 4092)
        assert mf.loadable_path(d, ModelFormat.GGUF).name == "Real.gguf"
