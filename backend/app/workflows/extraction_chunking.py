"""Split long notes into extraction-sized chunks and merge the results.

The extraction prompt produces JSON roughly 2–3× the size of its input, so a
long note cannot be extracted in one call without truncating mid-JSON. These
helpers are pure (no LLM, no I/O) so the policy is unit-testable.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.schemas.extraction import ExtractedRelationship, Extraction, Node

# Output for this prompt is ~2–3× the input; keep input to ~1/3.5 of the room.
_OUTPUT_TO_INPUT_RATIO = 2.5
# Starting ceiling per chunk regardless of context size — cloud models have
# large windows but bounded output limits, and very long single calls are slow
# to retry. This is only the seed: ``extraction_budget`` learns the real
# ceiling per model from truncation, since nothing reports it.
_DEFAULT_CHUNK_TOKENS = 4000
# Below this, a truncated chunk is not split again: its extraction fails.
MIN_SPLIT_TOKENS = 400

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


def _paragraphs(text: str) -> list[str]:
    """Blocks of lines separated by one or more blank lines."""
    blocks: list[list[str]] = [[]]
    for line in text.split("\n"):
        if line.strip():
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    return ["\n".join(b).strip() for b in blocks if b]


def _sentences(text: str) -> list[str]:
    """Split after ``.``, ``!`` or ``?`` when whitespace follows."""
    out: list[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in ".!?" and i + 1 < len(text) and text[i + 1].isspace():
            out.append(text[start : i + 1].strip())
            start = i + 1
    out.append(text[start:].strip())
    return [s for s in out if s]


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
    sentences = _sentences(unit)
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
    paragraphs = _paragraphs(text)
    units: list[str] = []
    for para in paragraphs:
        units.extend(_split_oversized(para, max_tokens, count))
    return [c.strip() for c in _pack(units, max_tokens, count, "\n\n") if c.strip()]




def merge_extractions(parts: list[Extraction]) -> Extraction:
    """Combine chunk extractions. An entity is the same entity only when its name is
    written identically; its descriptions from each chunk are joined in order."""
    nodes: dict[str, Node] = {}
    contexts: dict[str, list[str]] = {}
    rels: dict[tuple[str, str, str], ExtractedRelationship] = {}
    title: str | None = None
    for part in parts:
        title = title or part.title
        for node in part.nodes:
            nodes.setdefault(node.name, node)
            seen = contexts.setdefault(node.name, [])
            if node.isolated_context not in seen:
                seen.append(node.isolated_context)
        for rel in part.relationships:
            rels.setdefault((rel.source_name, rel.target_name, rel.relationship_type), rel)
    return Extraction(
        nodes=[Node(name=n.name, type=n.type, isolated_context=" ".join(contexts[n.name])) for n in nodes.values()],
        relationships=list(rels.values()),
        title=title,
    )
