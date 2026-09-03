"""Unit tests for local GGUF discovery: scanning, shards, refs, and caching."""

import pytest

from app.services import model_discovery as md
from tests.unit.test_gguf_metadata import CHAT_KV, build_gguf

# Discovery ignores files below the minimum GGUF size, so fixtures are padded.
PAD = md._MIN_GGUF_BYTES + 1024


def make_model(path, name="Test Model", extra=None):
    kv = {**CHAT_KV, "general.name": name}
    kv.update(extra or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_gguf(path, kv, pad_to=PAD)


class TestShardHandling:
    def test_first_shard_is_listed_continuations_are_not(self, tmp_path):
        first = tmp_path / "Big-Model-00001-of-00003.gguf"
        second = tmp_path / "Big-Model-00002-of-00003.gguf"
        assert md.is_shard_continuation(second) is True
        assert md.is_shard_continuation(first) is False

    def test_shard_count_read_from_name(self, tmp_path):
        assert md.shard_count(tmp_path / "M-00001-of-00007.gguf") == 7
        assert md.shard_count(tmp_path / "Plain.gguf") == 1

    def test_scan_returns_only_the_first_shard(self, tmp_path):
        for i in (1, 2, 3):
            make_model(tmp_path / f"Big-{i:05d}-of-00003.gguf")
        found = md.scan_dir_for_gguf(tmp_path)
        assert [p.name for p in found] == ["Big-00001-of-00003.gguf"]


class TestJunkFiltering:
    def test_appledouble_sidecars_are_skipped(self, tmp_path):
        """macOS writes ._name files on exFAT volumes; they are not models."""
        make_model(tmp_path / "Real.gguf")
        (tmp_path / "._Real.gguf").write_bytes(b"\0" * 4096)
        assert [p.name for p in md.scan_dir_for_gguf(tmp_path)] == ["Real.gguf"]

    def test_dotfiles_and_partials_are_skipped(self, tmp_path):
        make_model(tmp_path / "Real.gguf")
        (tmp_path / ".hidden.gguf").write_bytes(b"\0" * PAD)
        (tmp_path / "half.gguf.partial").write_bytes(b"\0" * PAD)
        assert [p.name for p in md.scan_dir_for_gguf(tmp_path)] == ["Real.gguf"]

    def test_tiny_files_are_skipped(self, tmp_path):
        (tmp_path / "stub.gguf").write_bytes(b"GGUF")
        assert md.scan_dir_for_gguf(tmp_path) == []


class TestScanPruning:
    def test_virtualenvs_are_not_descended_into(self, tmp_path):
        """Regression: rglob walked a ~100k-file venv under MODELS_DIR and hung."""
        make_model(tmp_path / "gguf" / "Real.gguf")
        make_model(tmp_path / ".venvs" / "lib" / "Stray.gguf")
        make_model(tmp_path / "node_modules" / "pkg" / "Stray.gguf")
        assert [p.name for p in md.scan_dir_for_gguf(tmp_path)] == ["Real.gguf"]

    def test_hidden_directories_are_pruned(self, tmp_path):
        make_model(tmp_path / "Visible.gguf")
        make_model(tmp_path / ".cache" / "Hidden.gguf")
        assert [p.name for p in md.scan_dir_for_gguf(tmp_path)] == ["Visible.gguf"]

    def test_depth_is_capped(self, tmp_path):
        make_model(tmp_path / "a" / "b" / "c" / "d" / "e" / "TooDeep.gguf")
        make_model(tmp_path / "a" / "Shallow.gguf")
        names = [p.name for p in md.scan_dir_for_gguf(tmp_path, max_depth=2)]
        assert names == ["Shallow.gguf"]

    def test_missing_root_is_not_an_error(self, tmp_path):
        assert md.scan_dir_for_gguf(tmp_path / "nope") == []


class TestModelRefs:
    def test_files_under_models_dir_get_relative_refs(self, tmp_path):
        """Relative refs survive the user moving MODELS_DIR to a faster disk."""
        path = tmp_path / "gguf" / "M.gguf"
        assert md.model_ref_for(path, tmp_path) == "gguf/M.gguf"

    def test_files_outside_models_dir_get_absolute_refs(self, tmp_path):
        outside = tmp_path.parent / "elsewhere" / "M.gguf"
        assert md.model_ref_for(outside, tmp_path) == str(outside.resolve())

    def test_relative_ref_resolves_against_models_dir(self, tmp_path):
        assert md.resolve_model_ref("gguf/M.gguf", tmp_path) == tmp_path / "gguf" / "M.gguf"

    def test_absolute_ref_resolves_as_is(self, tmp_path):
        p = tmp_path / "x" / "M.gguf"
        assert md.resolve_model_ref(str(p), tmp_path) == p

    def test_catalog_ids_are_not_path_refs(self, tmp_path):
        assert md.resolve_model_ref("gemma4-e4b-q4", tmp_path) is None
        assert md.resolve_model_ref("", tmp_path) is None

    def test_ref_round_trips(self, tmp_path):
        path = make_model(tmp_path / "gguf" / "Round.gguf")
        ref = md.model_ref_for(path, tmp_path)
        assert md.resolve_model_ref(ref, tmp_path).resolve() == path.resolve()


class TestDiscoverChatModels:
    def test_embedding_models_are_excluded(self, tmp_path):
        make_model(tmp_path / "Chat.gguf", name="Chatty")
        make_model(tmp_path / "Embed.gguf", name="Qwen3 Embedding 4B",
                   extra={"qwen3.pooling_type": 3})
        md.clear_cache()
        labels = [m.label for m in md.discover_chat_models(tmp_path)]
        assert labels == ["Chatty (7.5B)"]

    def test_rerankers_are_listed_with_a_warning(self, tmp_path):
        make_model(tmp_path / "Rerank.gguf", name="Qwen3 Reranker 4B")
        md.clear_cache()
        model = md.discover_chat_models(tmp_path)[0]
        assert any("reranker" in w.lower() for w in model.warnings)

    def test_missing_chat_template_warns(self, tmp_path):
        kv = {k: v for k, v in CHAT_KV.items() if k != "tokenizer.chat_template"}
        build_gguf(tmp_path / "Raw.gguf", {**kv, "general.name": "Raw Base"}, pad_to=PAD)
        md.clear_cache()
        model = md.discover_chat_models(tmp_path)[0]
        assert any("chat template" in w.lower() for w in model.warnings)

    def test_unreadable_files_are_skipped_not_fatal(self, tmp_path):
        make_model(tmp_path / "Good.gguf", name="Good")
        (tmp_path / "Bad.gguf").write_bytes(b"NOPE" + b"\0" * PAD)
        md.clear_cache()
        assert [m.label for m in md.discover_chat_models(tmp_path)] == ["Good (7.5B)"]

    def test_results_are_sorted_and_carry_metadata(self, tmp_path):
        make_model(tmp_path / "b.gguf", name="Beta")
        make_model(tmp_path / "a.gguf", name="Alpha")
        md.clear_cache()
        models = md.discover_chat_models(tmp_path)
        assert [m.label for m in models] == ["Alpha (7.5B)", "Beta (7.5B)"]
        assert models[0].architecture == "gemma4"
        assert models[0].context_length == 131072

    def test_empty_dir_yields_nothing(self, tmp_path):
        md.clear_cache()
        assert md.discover_chat_models(tmp_path) == []

    def test_to_dict_is_json_friendly(self, tmp_path):
        make_model(tmp_path / "M.gguf", name="Qwen3 Reranker 4B")
        md.clear_cache()
        data = md.discover_chat_models(tmp_path)[0].to_dict()
        assert isinstance(data["warnings"], list)
        assert set(data) >= {"ref", "path", "label", "architecture", "size_gb"}


class TestMetadataCache:
    def test_second_scan_does_not_reread_headers(self, tmp_path, monkeypatch):
        make_model(tmp_path / "M.gguf")
        md.clear_cache()
        calls = []
        real = md.try_read_gguf_metadata

        def counting(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(md, "try_read_gguf_metadata", counting)
        md.discover_chat_models(tmp_path)
        md.discover_chat_models(tmp_path)
        assert len(calls) == 1

    def test_cache_invalidated_when_the_file_changes(self, tmp_path, monkeypatch):
        path = make_model(tmp_path / "M.gguf", name="First")
        md.clear_cache()
        assert md.discover_chat_models(tmp_path)[0].label.startswith("First")
        make_model(path, name="Second")
        import os
        stat = path.stat()
        os.utime(path, (stat.st_atime + 10, stat.st_mtime + 10))
        assert md.discover_chat_models(tmp_path)[0].label.startswith("Second")


class TestInspectChatModel:
    def test_valid_model_passes(self, tmp_path):
        info, error = md.inspect_chat_model(make_model(tmp_path / "M.gguf"))
        assert error is None and info is not None

    def test_missing_file(self, tmp_path):
        _, error = md.inspect_chat_model(tmp_path / "nope.gguf")
        assert "No such file" in error

    def test_directory_is_rejected(self, tmp_path):
        (tmp_path / "adir.gguf").mkdir()
        _, error = md.inspect_chat_model(tmp_path / "adir.gguf")
        assert "Not a file" in error

    def test_non_gguf_is_rejected(self, tmp_path):
        (tmp_path / "x.gguf").write_bytes(b"nope" * 100)
        _, error = md.inspect_chat_model(tmp_path / "x.gguf")
        assert "not a readable GGUF" in error

    def test_embedding_model_is_rejected_for_chat(self, tmp_path):
        path = make_model(tmp_path / "E.gguf", name="Qwen3 Embedding 4B",
                          extra={"qwen3.pooling_type": 3})
        _, error = md.inspect_chat_model(path)
        assert "embedding model" in error

    def test_shard_continuation_points_at_the_first_shard(self, tmp_path):
        path = make_model(tmp_path / "M-00002-of-00003.gguf")
        _, error = md.inspect_chat_model(path)
        assert "first shard" in error
