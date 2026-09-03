"""Unit tests for the dependency-free GGUF header reader.

Fixtures are synthesised byte-for-byte to the GGUF spec so the parser is tested
against known input rather than whatever models happen to be on the machine.
"""

import struct

import pytest

from app.services.gguf_metadata import (
    GgufError,
    read_gguf_metadata,
    try_read_gguf_metadata,
)

# GGUF value type ids
T_UINT32, T_FLOAT32, T_BOOL, T_STRING, T_ARRAY, T_UINT64 = 4, 6, 7, 8, 9, 10


def _u64(n: int) -> bytes:
    return struct.pack("<Q", n)


def _gguf_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _u64(len(raw)) + raw


def _encode_value(value, value_type=None) -> tuple[int, bytes]:
    """Encode a Python value as (type_id, bytes), inferring the type when omitted."""
    if value_type is not None:
        if value_type == T_UINT32:
            return T_UINT32, struct.pack("<I", value)
        if value_type == T_UINT64:
            return T_UINT64, struct.pack("<Q", value)
        if value_type == T_FLOAT32:
            return T_FLOAT32, struct.pack("<f", value)
        if value_type == T_BOOL:
            return T_BOOL, struct.pack("<?", value)
        raise AssertionError(f"unhandled explicit type {value_type}")
    if isinstance(value, str):
        return T_STRING, _gguf_string(value)
    if isinstance(value, bool):
        return T_BOOL, struct.pack("<?", value)
    if isinstance(value, int):
        return T_UINT32, struct.pack("<I", value)
    if isinstance(value, float):
        return T_FLOAT32, struct.pack("<f", value)
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            body = struct.pack("<I", T_STRING) + _u64(len(value))
            body += b"".join(_gguf_string(v) for v in value)
            return T_ARRAY, body
        body = struct.pack("<I", T_UINT32) + _u64(len(value))
        body += b"".join(struct.pack("<I", v) for v in value)
        return T_ARRAY, body
    raise AssertionError(f"unhandled value {value!r}")


def build_gguf(path, kv: dict, *, tensor_count=0, pad_to=0, magic=b"GGUF", version=3):
    """Write a syntactically valid GGUF header with the given metadata."""
    body = b""
    for key, value in kv.items():
        vtype, encoded = _encode_value(value)
        body += _gguf_string(key) + struct.pack("<I", vtype) + encoded
    blob = magic + struct.pack("<I", version) + _u64(tensor_count) + _u64(len(kv)) + body
    if pad_to and len(blob) < pad_to:
        blob += b"\0" * (pad_to - len(blob))
    path.write_bytes(blob)
    return path


CHAT_KV = {
    "general.architecture": "gemma4",
    "general.name": "Gemma 4 E4B It",
    "general.size_label": "7.5B",
    "general.file_type": 15,
    "gemma4.context_length": 131072,
    "gemma4.block_count": 34,
    "tokenizer.chat_template": "{% for m in messages %}{{ m.content }}{% endfor %}",
    "tokenizer.ggml.tokens": ["<bos>", "hello", "world"],
}


class TestReadValidFiles:
    def test_reads_core_fields(self, tmp_path):
        info = read_gguf_metadata(build_gguf(tmp_path / "chat.gguf", CHAT_KV, tensor_count=291))
        assert info.architecture == "gemma4"
        assert info.name == "Gemma 4 E4B It"
        assert info.size_label == "7.5B"
        assert info.context_length == 131072
        assert info.block_count == 34
        assert info.file_type == 15
        assert info.tensor_count == 291
        assert info.has_chat_template is True

    def test_chat_model_is_not_an_embedding_model(self, tmp_path):
        info = read_gguf_metadata(build_gguf(tmp_path / "chat.gguf", CHAT_KV))
        assert info.pooling_type is None
        assert info.is_embedding_model is False

    def test_pooling_type_marks_an_embedding_model(self, tmp_path):
        kv = {**CHAT_KV, "general.name": "Qwen3 Embedding 4B", "qwen3.pooling_type": 3}
        info = read_gguf_metadata(build_gguf(tmp_path / "embed.gguf", kv))
        assert info.pooling_type == 3
        assert info.is_embedding_model is True

    def test_chat_template_alone_does_not_imply_chat_role(self, tmp_path):
        """Regression: embed/rerank GGUFs from instruct models carry chat templates."""
        kv = {**CHAT_KV, "qwen3.pooling_type": 3}
        info = read_gguf_metadata(build_gguf(tmp_path / "e.gguf", kv))
        assert info.has_chat_template is True
        assert info.is_embedding_model is True

    def test_string_arrays_are_skipped_without_losing_position(self, tmp_path):
        """A 3-token vocab sits between two wanted keys; both must still be read."""
        kv = {
            "general.architecture": "llama",
            "tokenizer.ggml.tokens": ["a", "b", "c"],
            "llama.context_length": 4096,
        }
        info = read_gguf_metadata(build_gguf(tmp_path / "m.gguf", kv))
        assert info.architecture == "llama"
        assert info.context_length == 4096

    def test_numeric_arrays_are_skipped(self, tmp_path):
        kv = {
            "general.architecture": "llama",
            "tokenizer.ggml.token_type": [1, 2, 3, 4, 5],
            "llama.context_length": 8192,
        }
        assert read_gguf_metadata(build_gguf(tmp_path / "m.gguf", kv)).context_length == 8192

    def test_context_length_found_under_any_architecture_prefix(self, tmp_path):
        kv = {"general.architecture": "qwen3", "qwen3.context_length": 40960}
        assert read_gguf_metadata(build_gguf(tmp_path / "q.gguf", kv)).context_length == 40960

    def test_missing_optional_fields_are_none(self, tmp_path):
        info = read_gguf_metadata(build_gguf(tmp_path / "bare.gguf", {"general.architecture": "x"}))
        assert info.context_length is None
        assert info.name == ""
        assert info.has_chat_template is False

    def test_display_name_falls_back_to_filename(self, tmp_path):
        info = read_gguf_metadata(build_gguf(tmp_path / "My-Model.gguf", {"general.architecture": "x"}))
        assert info.display_name == "My-Model"

    def test_size_gb_reflects_file_size(self, tmp_path):
        path = build_gguf(tmp_path / "m.gguf", {"general.architecture": "x"}, pad_to=2 * 1024**2)
        assert read_gguf_metadata(path).size_gb == pytest.approx(0.0, abs=0.01)
        assert read_gguf_metadata(path).size_bytes >= 2 * 1024**2

    def test_bool_and_float_values_parse(self, tmp_path):
        kv = {
            "general.architecture": "llama",
            "tokenizer.ggml.add_eos_token": True,
            "llama.attention.layer_norm_rms_epsilon": 1e-6,
            "llama.context_length": 2048,
        }
        assert read_gguf_metadata(build_gguf(tmp_path / "m.gguf", kv)).context_length == 2048


class TestRejectsBadFiles:
    def test_wrong_magic(self, tmp_path):
        path = build_gguf(tmp_path / "x.gguf", {"general.architecture": "x"}, magic=b"NOPE")
        with pytest.raises(GgufError, match="not a GGUF file"):
            read_gguf_metadata(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(GgufError, match="Cannot stat"):
            read_gguf_metadata(tmp_path / "nope.gguf")

    def test_truncated_midway(self, tmp_path):
        path = build_gguf(tmp_path / "t.gguf", CHAT_KV)
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        with pytest.raises(GgufError, match="Unexpected end of file"):
            read_gguf_metadata(path)

    def test_header_only_then_eof(self, tmp_path):
        (tmp_path / "h.gguf").write_bytes(b"GGUF" + struct.pack("<I", 3) + _u64(0) + _u64(5))
        with pytest.raises(GgufError, match="Unexpected end of file"):
            read_gguf_metadata(tmp_path / "h.gguf")

    def test_unknown_value_type(self, tmp_path):
        blob = (
            b"GGUF" + struct.pack("<I", 3) + _u64(0) + _u64(1)
            + _gguf_string("weird") + struct.pack("<I", 99)
        )
        (tmp_path / "w.gguf").write_bytes(blob)
        with pytest.raises(GgufError, match="Unknown GGUF value type"):
            read_gguf_metadata(tmp_path / "w.gguf")

    def test_absurd_kv_count_is_refused_before_reading(self, tmp_path):
        blob = b"GGUF" + struct.pack("<I", 3) + _u64(0) + _u64(10**9)
        (tmp_path / "big.gguf").write_bytes(blob)
        with pytest.raises(GgufError, match="Refusing to read"):
            read_gguf_metadata(tmp_path / "big.gguf")

    def test_absurd_string_length_is_refused(self, tmp_path):
        blob = b"GGUF" + struct.pack("<I", 3) + _u64(0) + _u64(1) + _u64(10**12)
        (tmp_path / "s.gguf").write_bytes(blob)
        with pytest.raises(GgufError, match="Refusing to read"):
            read_gguf_metadata(tmp_path / "s.gguf")

    def test_absurd_array_count_is_refused(self, tmp_path):
        blob = (
            b"GGUF" + struct.pack("<I", 3) + _u64(0) + _u64(1)
            + _gguf_string("tokenizer.ggml.tokens")
            + struct.pack("<I", T_ARRAY) + struct.pack("<I", T_STRING) + _u64(10**10)
        )
        (tmp_path / "a.gguf").write_bytes(blob)
        with pytest.raises(GgufError, match="Refusing to read"):
            read_gguf_metadata(tmp_path / "a.gguf")


class TestTryRead:
    def test_returns_none_instead_of_raising(self, tmp_path):
        (tmp_path / "junk.gguf").write_bytes(b"not a model")
        assert try_read_gguf_metadata(tmp_path / "junk.gguf") is None
        assert try_read_gguf_metadata(tmp_path / "absent.gguf") is None

    def test_returns_info_for_valid_file(self, tmp_path):
        assert try_read_gguf_metadata(build_gguf(tmp_path / "ok.gguf", CHAT_KV)) is not None


class TestArchitectureNamespacing:
    def test_architecture_prefixed_key_wins_over_a_foreign_namespace(self, tmp_path):
        """A file declaring llama must report llama's context, not another arch's."""
        kv = {
            "general.architecture": "llama",
            "gemma4.context_length": 131072,
            "llama.context_length": 8192,
        }
        assert read_gguf_metadata(build_gguf(tmp_path / "m.gguf", kv)).context_length == 8192

    def test_falls_back_to_any_namespace_when_arch_key_absent(self, tmp_path):
        kv = {"general.architecture": "llama", "qwen3.context_length": 40960}
        assert read_gguf_metadata(build_gguf(tmp_path / "m.gguf", kv)).context_length == 40960

    def test_pooling_type_respects_architecture_too(self, tmp_path):
        kv = {"general.architecture": "qwen3", "qwen3.pooling_type": 3}
        assert read_gguf_metadata(build_gguf(tmp_path / "m.gguf", kv)).is_embedding_model is True
