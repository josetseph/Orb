"""Unit tests for GGUF layout helpers: shards, magic bytes, name advisories."""

from app.services import model_formats as mf


def gguf_file(path, size=64 * 1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * size)
    return path


class TestShardsAndLoadablePath:
    def test_only_the_first_shard_is_loadable(self, tmp_path):
        d = tmp_path / "split"
        for i in (1, 2, 3):
            gguf_file(d / f"Big-{i:05d}-of-00003.gguf")
        assert mf.loadable_path(d).name == "Big-00001-of-00003.gguf"

    def test_shard_continuation_detected(self, tmp_path):
        assert mf.is_shard_continuation(tmp_path / "M-00002-of-00003.gguf") is True
        assert mf.is_shard_continuation(tmp_path / "M-00001-of-00003.gguf") is False
        assert mf.is_shard_continuation(tmp_path / "Plain.gguf") is False

    def test_file_path_passes_through(self, tmp_path):
        f = gguf_file(tmp_path / "m.gguf")
        assert mf.loadable_path(f) == f

    def test_folder_without_gguf_is_returned_as_is(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert mf.loadable_path(tmp_path / "empty") == tmp_path / "empty"


class TestGgufMagic:
    def test_appledouble_sidecar_is_not_a_model(self, tmp_path):
        """macOS writes 4 KB ._name files beside models on exFAT volumes."""
        sidecar = tmp_path / "._Real-Model.gguf"
        sidecar.write_bytes(b"\x00\x05\x16\x07" + b"\0" * 4092)
        assert mf.looks_like_gguf(sidecar) is False

    def test_extension_alone_is_not_enough(self, tmp_path):
        fake = tmp_path / "fake.gguf"
        fake.write_bytes(b"not a model" * 100)
        assert mf.looks_like_gguf(fake) is False
        assert mf.looks_like_gguf(gguf_file(tmp_path / "real.gguf")) is True

    def test_sidecars_are_skipped_when_scanning_a_folder(self, tmp_path):
        d = tmp_path / "models"
        gguf_file(d / "Real.gguf")
        (d / "._Real.gguf").write_bytes(b"\x00\x05\x16\x07" + b"\0" * 4092)
        assert mf.loadable_path(d).name == "Real.gguf"


class TestNameWarnings:
    def test_reranker_and_embedder_names_warn(self):
        assert any("reranker" in w.lower() for w in mf.name_warnings("Qwen3-Reranker-4B.gguf"))
        assert any("embedding" in w.lower() for w in mf.name_warnings("Qwen3-Embedding-4B.gguf"))
        assert mf.name_warnings("Gemma-4-E4B.gguf") == []
