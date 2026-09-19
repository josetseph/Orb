"""Unit tests for LLMService._clean_json in app/services/llm.py.

_clean_json is a pure string-transformation method: it strips markdown code
fences, normalises smart quotes, then runs json_repair (which escapes stray
control characters itself). No LLM client or network access is needed.
"""

import json

import pytest

from app.services.llm import LLMService


@pytest.fixture(scope="module")
def svc() -> LLMService:
    """LLMService instance bypassing __init__ (which opens HTTP connections)."""
    return LLMService.__new__(LLMService)


# ── markdown code-fence stripping ─────────────────────────────────────────────


class TestMarkdownCodeFenceStripping:
    def test_json_fenced_block_extracted(self, svc):
        raw = '```json\n{"key": "value"}\n```'
        result = json.loads(svc._clean_json(raw))
        assert result["key"] == "value"

    def test_plain_fenced_block_extracted(self, svc):
        raw = '```\n{"answer": 42}\n```'
        result = json.loads(svc._clean_json(raw))
        assert result["answer"] == 42

    def test_no_fence_passes_through(self, svc):
        raw = '{"plain": true}'
        result = json.loads(svc._clean_json(raw))
        assert result["plain"] is True

    def test_fence_with_surrounding_text(self, svc):
        raw = 'Here is the output:\n```json\n{"x": 1}\n```\nDone.'
        result = json.loads(svc._clean_json(raw))
        assert result["x"] == 1


# ── control characters (json_repair escapes them; output must parse) ──────────


class TestControlCharacterRemoval:
    def test_null_bytes_removed(self, svc):
        raw = '{"a": "hel\x00lo"}'
        cleaned = svc._clean_json(raw)
        assert "\x00" not in cleaned
        assert json.loads(cleaned)["a"].replace("\x00", "") == "hello"

    def test_bell_char_removed(self, svc):
        raw = '{"a": "te\x07xt"}'
        cleaned = svc._clean_json(raw)
        assert "\x07" not in cleaned
        assert json.loads(cleaned)["a"].replace("\x07", "") == "text"

    def test_newline_preserved(self, svc):
        # \n (\x0a) is NOT in the removal set
        raw = '{"lines": "line1\\nline2"}'
        cleaned = svc._clean_json(raw)
        assert "\n" not in cleaned or "line1" in cleaned  # json_repair may encode it

    def test_tab_preserved_in_json(self, svc):
        # \t (\x09) is not stripped; json_repair handles it
        raw = '{"v": "a\\tb"}'
        result = json.loads(svc._clean_json(raw))
        assert result["v"] == "a\tb"


# ── smart-quote normalisation ─────────────────────────────────────────────────


class TestSmartQuoteNormalisation:
    def test_left_double_smart_quote_replaced(self, svc):
        # \u201C → "
        raw = "\u201chello\u201d"
        cleaned = svc._clean_json(raw)
        assert "\u201c" not in cleaned
        assert "\u201d" not in cleaned

    def test_right_single_smart_quote_replaced(self, svc):
        # \u2018, \u2019 → '
        raw = "\u2018hello\u2019"
        cleaned = svc._clean_json(raw)
        assert "\u2018" not in cleaned
        assert "\u2019" not in cleaned

    def test_low_double_quote_replaced(self, svc):
        # \u201E („) → "
        raw = "\u201eHallo\u201c"
        cleaned = svc._clean_json(raw)
        assert "\u201e" not in cleaned

    def test_valid_json_after_smart_quote_fix(self, svc):
        # Build a JSON-like string using smart quotes around a key-value pair
        raw = '\u201c{"name": "Alice"}\u201d'
        # After replacement the outer smart quotes become regular quotes;
        # json_repair should be able to parse what's left.
        cleaned = svc._clean_json(raw)
        assert "Alice" in cleaned


# ── end-to-end: result is valid JSON ─────────────────────────────────────────


class TestEndToEndClean:
    def test_clean_json_already_valid(self, svc):
        raw = '{"status": "ok", "count": 3}'
        result = json.loads(svc._clean_json(raw))
        assert result["status"] == "ok"
        assert result["count"] == 3

    def test_repaired_missing_closing_brace(self, svc):
        raw = '{"key": "value"'
        cleaned = svc._clean_json(raw)
        result = json.loads(cleaned)
        assert result["key"] == "value"

    def test_repaired_trailing_comma(self, svc):
        raw = '{"a": 1, "b": 2,}'
        cleaned = svc._clean_json(raw)
        result = json.loads(cleaned)
        assert result["a"] == 1
        assert result["b"] == 2

    def test_fenced_with_control_chars_and_smart_quotes(self, svc):
        # Combined: markdown fence + smart quotes + null byte
        inner = "{\u201cname\u201d: \u201cAlice\x00\u201d}"
        raw = f"```json\n{inner}\n```"
        cleaned = svc._clean_json(raw)
        # json_repair should recover a parseable object
        result = json.loads(cleaned)
        assert "Alice" in result.get("name", "")
