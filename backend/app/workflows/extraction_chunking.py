"""Split long notes into extraction-sized chunks and merge the results.

The extraction prompt produces JSON roughly 2–3× the size of its input, so a
long note cannot be extracted in one call without truncating mid-JSON. These
helpers are pure (no LLM, no I/O) so the policy is unit-testable.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from app.schemas.extraction import ExtractedRelationship, Extraction, Node

# Output for this prompt is ~2–3× the input; keep input to ~1/3.5 of the room.
_OUTPUT_TO_INPUT_RATIO = 2.5
# Starting ceiling per chunk regardless of context size — cloud models have
# large windows but bounded output limits, and very long single calls are slow
# to retry. This is only the seed: ``extraction_budget`` learns the real
# ceiling per model from truncation, since nothing reports it.
_DEFAULT_CHUNK_TOKENS = 4000
# Below this, a truncated chunk is accepted (repaired) rather than split again.
MIN_SPLIT_TOKENS = 400

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = {"the", "and", "with", "from", "for", "that", "this", "model", "method", "system", "systems"}


def sentences_about(name: str, text: str, *, max_sentences: int = 4, max_chars: int = 600) -> str:
    """The sentences of ``text`` that mention ``name``, in document order.

    The fallback when the model returned no context for an entity: a few
    sentences about it, never the whole note — a 100k-character note stored as
    one entity's context swamps its embedding and its detail panel alike.
    Empty when the name never appears.
    """
    needle = (name or "").strip().lower()
    if not needle or not text:
        return ""
    sentences = [x.strip() for x in _SENTENCE_RE.split(re.sub(r"\s+", " ", text))]
    # Exact name first; the model often canonicalises ("Waterfall Model" for a
    # note that says "the waterfall approach"), so fall back to every word of
    # the name, then to its one distinctive word.
    words = [w for w in re.findall(r"[a-z0-9]+", needle) if len(w) > 3 and w not in _STOPWORDS]
    tests = [lambda t: needle in t]
    if words:
        tests.append(lambda t: all(w in t for w in words))
        if len(words) > 1:
            longest = max(words, key=len)
            tests.append(lambda t: longest in t)
    matches = next((m for m in ([x for x in sentences if t(x.lower())] for t in tests) if m), [])
    picked: list[str] = []
    used = 0
    for sentence in matches:
        if needle not in sentence.lower():
            needle = next((w for w in words if w in sentence.lower()), needle)
        if len(sentence) > max_chars:
            # A single monster "sentence" (a table row, a transcript run): keep
            # the window around the first mention.
            at = sentence.lower().index(needle)
            lo = max(0, at - max_chars // 2)
            sentence = ("…" if lo else "") + sentence[lo : lo + max_chars].strip() + "…"
        if used + len(sentence) > max_chars * max_sentences:
            break
        picked.append(sentence)
        used += len(sentence)
        if len(picked) >= max_sentences:
            break
    return " ".join(picked)


def chunk_token_budget(
    context_tokens: int,
    prompt_overhead_tokens: int,
    model: str | None = None,
) -> int:
    """Largest input chunk that should come back whole.

    Two limits apply. The context has to hold prompt + input + expected output,
    which is arithmetic. The model's *output* ceiling is the one that actually
    binds and is not reported by any API, so it is learned per model from
    truncations; ``_DEFAULT_CHUNK_TOKENS`` is only where a new model starts.
    """
    from app.services.extraction_budget import learned_budget

    ceiling = learned_budget(model, _DEFAULT_CHUNK_TOKENS)
    room = context_tokens - prompt_overhead_tokens - 64
    fits = int(room / (1 + _OUTPUT_TO_INPUT_RATIO))
    return max(MIN_SPLIT_TOKENS, min(ceiling, fits))


def _pack(units: list[str], max_tokens: int, count: Callable[[str], int], sep: str) -> list[str]:
    """Greedily pack ``units`` into chunks no larger than ``max_tokens``."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = count(unit)
        if current and current_tokens + unit_tokens > max_tokens:
            chunks.append(sep.join(current))
            current, current_tokens = [], 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        chunks.append(sep.join(current))
    return chunks


def _split_oversized(unit: str, max_tokens: int, count: Callable[[str], int]) -> list[str]:
    """Break one paragraph that alone exceeds the budget: lines → sentences → characters."""
    if count(unit) <= max_tokens:
        return [unit]
    lines = [ln for ln in unit.split("\n") if ln.strip()]
    if len(lines) > 1:
        out: list[str] = []
        for piece in _pack(lines, max_tokens, count, "\n"):
            out.extend(_split_oversized(piece, max_tokens, count))
        return out
    sentences = [s for s in _SENTENCE_RE.split(unit) if s.strip()]
    if len(sentences) > 1:
        out = []
        for piece in _pack(sentences, max_tokens, count, " "):
            out.extend(_split_oversized(piece, max_tokens, count))
        return out
    words = unit.split()
    if len(words) > 1:
        out = []
        for piece in _pack(words, max_tokens, count, " "):
            out.extend(_split_oversized(piece, max_tokens, count))
        return out
    # A single token-dense word (URL, hash, …): hard-split by characters.
    approx_chars = max(1, int(len(unit) * max_tokens / max(count(unit), 1)))
    return [unit[i : i + approx_chars] for i in range(0, len(unit), approx_chars)]


def split_for_extraction(
    text: str, max_tokens: int, count: Callable[[str], int]
) -> list[str]:
    """Split ``text`` on paragraph boundaries into chunks of at most ``max_tokens``.

    Returns ``[text]`` unchanged when it already fits, and never returns empty
    chunks. Paragraphs are kept whole where possible so entity context is not
    cut mid-thought.
    """
    text = (text or "").strip()
    if not text:
        return []
    max_tokens = max(1, int(max_tokens))
    if count(text) <= max_tokens:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        units.extend(_split_oversized(para, max_tokens, count))
    return [c.strip() for c in _pack(units, max_tokens, count, "\n\n") if c.strip()]


def normalize_entity_name(name: str) -> str:
    """Canonical key for matching an entity across passes and chunks.

    Shared with the task-split extractor on purpose: if the two disagreed, a
    relationship or context would be dropped for referencing an entity that
    merging considers the same one.
    """
    return (name or "").lstrip("#").strip().lower()


def _norm(name: str) -> str:
    return (name or "").lstrip("#").strip().lower()


def merge_extractions(parts: list[Extraction]) -> Extraction:
    """Combine chunk extractions: dedupe nodes by name, concatenate their contexts."""
    nodes: dict[str, Node] = {}
    contexts: dict[str, list[str]] = {}
    rels: dict[tuple[str, str, str], ExtractedRelationship] = {}
    title: str | None = None
    sentiment: str | None = None

    for part in parts:
        if part is None:
            continue
        if not title and (part.title or "").strip():
            title = part.title.strip()
        if not sentiment and part.sentiment:
            sentiment = part.sentiment
        for node in part.nodes:
            key = _norm(node.name)
            if not key:
                continue
            ctx = (node.isolated_context or "").strip()
            if key not in nodes:
                nodes[key] = node.model_copy()
                contexts[key] = [ctx] if ctx else []
            else:
                existing = nodes[key]
                if (existing.type or "thing").lower() == "thing" and node.type:
                    existing.type = node.type
                if ctx and ctx not in contexts[key]:
                    contexts[key].append(ctx)
        for rel in part.relationships:
            key = (
                _norm(rel.source_name),
                _norm(rel.target_name),
                (rel.relationship_type or "").strip().lower(),
            )
            if not key[0] or not key[1]:
                continue
            if key not in rels or rel.confidence > rels[key].confidence:
                rels[key] = rel

    for key, node in nodes.items():
        node.isolated_context = " ".join(contexts[key])

    return Extraction(
        nodes=list(nodes.values()),
        relationships=list(rels.values()),
        sentiment=sentiment or "Neutral",
        title=title,
    )
