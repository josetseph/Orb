"""LangGraph ingestion agent: multimodal → extraction → storage → indexing."""

# pylint: disable=import-outside-toplevel,protected-access
import asyncio
import re
import uuid
from datetime import datetime
from typing import Any, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.core.config import settings
from app.core.log import get_logger
from app.schemas.extraction import Extraction, NoteInput
from app.services.multimedia import multimedia_service
from app.workflows.extraction_chunking import (
    MIN_SPLIT_TOKENS,
    chunk_token_budget,
    merge_extractions,
    split_for_extraction,
)

logger = get_logger("IngestionPipeline")


def _require_workflow(state: "IngestionState"):
    """Return the KB-scoped IngestionWorkflow; never fall back to a global default."""
    wf = state.get("workflow")
    if wf is None:
        raise RuntimeError(
            "Ingestion agent requires state['workflow'] "
            "(KB-scoped IngestionWorkflow from process_note)"
        )
    return wf

# Heavy local model multimedia extraction is serialized by default so Florence,
# Whisper, and Marlin do not compete for the same CPU/RAM budget.
multimedia_concurrency_limit = asyncio.Semaphore(settings.MULTIMEDIA_CONCURRENCY)

# Blocks appended by multimodal_node — strip before re-processing so re-ingest
# does not duplicate Florence/Whisper/Marlin output in the vault .md.
_ENRICHMENT_BLOCK_RE = re.compile(
    r"\n\n\[(?:"
    r"PDF Extraction \([^\]]+\)|"
    r"Image:[^\]]+|"
    r"Audio Transcript \([^\]]+\)|"
    r"Video Audio Transcript \([^\]]+\)|"
    r"Video Visual Analysis \([^\]]+\)|"
    r"Word Extraction \([^\]]+\)|"
    r"Spreadsheet Extraction \([^\]]+\)"
    r")\]"
)


def _strip_prior_multimedia_enrichment(content: str) -> str:
    """Remove previously appended extraction/transcript blocks; keep user body + links."""
    if not content:
        return content or ""
    match = _ENRICHMENT_BLOCK_RE.search(content)
    if not match:
        return content
    return content[: match.start()].rstrip()


def _build_extraction_prompt(extraction_content: str) -> str:
    """Knowledge Architect prompt for one note (or one chunk of a long note)."""
    return f"""You are a precision knowledge extraction engine. Your sole function is to decompose any input note into a fully structured knowledge graph — extracting every entity, every relationship, and generating an isolated contextual description for each entity as it exists *within the note only*.  # pylint: disable=line-too-long

---

## CORE RULES

- The note may be anything: personal notes, lecture or course material, company or technical documentation, meeting records, research. Extract what the text says. Do not assume the reader wrote it, and do not describe it as someone's personal knowledge.
- Extract **every** entity, no matter how minor. Do not skip implicit or background entities.
- Do **not** use outside knowledge. Every piece of context must be grounded in the note.
- Do **not** hallucinate relationships. If it isn't stated or strongly implied by the text, it doesn't exist.
- Co-reference resolution is mandatory: if "he", "she", "it", "they", "the city", "the war" refers to a named entity, map it back to that entity — do not treat pronouns as separate entities.
- Every relationship must be directional. Use `->` for one-way and `<->` for mutual/bidirectional.
- Entity names must be **canonical** — pick one consistent name per entity (e.g., don't list "Adwa" and "Battle of Adwa" as separate entities if they refer to the same thing).

---

## STEP-BY-STEP PROCESS

Follow these steps in order. Do not skip any.

### STEP 1 — Entity Extraction
Identify every distinct entity in the note. For each, assign:
- `name`: The canonical name of the entity.
- `type`: The following are examples and not an exhaustive list — use your judgment to classify each entity into the most fitting type based on its nature and role in the note:
  - `Person` — a human individual
  - `Place` — a physical or geographic location
  - `Organization` — a group, nation, army, or institution (governments, companies, teams, schools)
  - `Event` — a specific occurrence with a defined scope
  - `Work` — a creative or intellectual work such as a book, film, TV series, album, article, or artwork
  - `Thing` — a physical object, document, or artifact
  - `Concept` — an abstract idea, principle, or condition (e.g., "sovereignty", "friendship")
  - `Time Period` — a specific date, duration, era, or recurring time (e.g., "Weekend", "2 March 1896")
- `type_reasoning`: One sentence explaining why you chose this type for this entity. Be specific — mention the key clue from the note that determined the classification.

### STEP 2 — Relationship Extraction
List every relationship between entities. For each:
- `source_name`: The entity the relationship originates from.
- `target_name`: The entity the relationship points to.
- `relationship_type`: A concise snake_case verb phrase (e.g. `attends`, `lives_in`, `is_friends_with`).
- `natural_language`: A short natural-language description of the relationship (e.g. "attends school").
- `reasoning`: One sentence citing the specific word, phrase, or sentence in the note that supports this relationship.
- Only include what the text explicitly states or directly implies.

### STEP 3 — Node Context Generation
For each node, write a tightly focused contextual description using **only information from the note**.
Rules:
- Write in complete sentences
- Stay entity-centric: everything in the description should orbit *this specific entity*
- Include all roles, relationships, attributes, and key factual properties that describe the entity but don't involve another entity
- All context must be drawn from the note — do not add any outside information or assumptions, even if they seem obvious. If it's not in the note, it doesn't exist for the entity's context.
- Do not add assumptions or outside knowledge
- Length: unlimited number of sentences depending on how much the note says about the entity

---

## OUTPUT FORMAT

Return a single JSON object structured exactly like this:

{{
  "title": "string — descriptive title that captures the main subject of this note",
  "nodes": [
    {{
      "name": "string — canonical entity name",
      "type": "string — the most fitting type for this entity",
      "type_reasoning": "string — explaining why you chose this type, citing the key clue from the note",
      "isolated_context": "string — isolated, entity-centric contextual paragraph drawn entirely from the note"
    }}
  ],
  "relationships": [
    {{
      "source_name": "string — the entity the relationship originates from",
      "target_name": "string — the entity the relationship points to",
      "relationship_type": "string — concise snake_case verb phrase (e.g. attends, lives_in, is_friends_with)",
      "natural_language": "string — short natural-language description of the relationship",
      "reasoning": "string — one sentence citing the specific text that supports this relationship"
    }}
  ]
}}

---

## WORKED EXAMPLE

**Input Note:**
"Ama and Kofi are friends. Ama is a girl in primary school. Kofi is a boy who plays in the neighborhood. Ama likes to play with Kofi every weekend after she is done with her homework."

**Output:**
{{
  "title": "Ama and Kofi's Weekend Friendship",
  "nodes": [
    {{
      "name": "Ama",
      "type": "Person",
      "type_reasoning": "Ama is explicitly described as a girl, making her a human individual.",
      "isolated_context": "Ama is a girl and a student at Primary School. She is mutual friends with Kofi and shares a weekend play routine with him. She lives in the same neighborhood as Kofi. She consistently completes her homework before engaging in play."
    }},
    {{
      "name": "Kofi",
      "type": "Person",
      "type_reasoning": "Kofi is explicitly described as a boy, making him a human individual.",
      "isolated_context": "Kofi is a boy who lives in the Neighborhood. He is mutual friends with Ama and plays with her every weekend. His play is situated within the neighborhood."
    }},
    {{
      "name": "Primary School",
      "type": "Place",
      "type_reasoning": "Primary School is an educational institution — a physical location that Ama attends.",
      "isolated_context": "Primary School is the educational institution that Ama attends. It is the only institution mentioned in the note and defines Ama's role as a student."
    }},
    {{
      "name": "Neighborhood",
      "type": "Place",
      "type_reasoning": "The Neighborhood is a physical geographic area where both Ama and Kofi live and play.",
      "isolated_context": "The Neighborhood is a shared residential area where both Ama and Kofi live. It is also where Kofi plays."
    }},
    {{
      "name": "Weekend",
      "type": "Time Period",
      "type_reasoning": "Weekend is a recurring temporal interval — a defined period of time during which events in the note occur.",
      "isolated_context": "The Weekend is the recurring time period during which Ama and Kofi play together. It is contingent on Ama finishing her homework first."
    }},
    {{
      "name": "Homework",
      "type": "Thing",
      "type_reasoning": "Homework is a concrete recurring task/artifact that Ama must complete — a physical obligation rather than an abstract concept.",
      "isolated_context": "Homework is a recurring obligation that Ama must complete before she is free to play with Kofi on the Weekend. It acts as a precondition to their shared leisure activity."
    }}
  ],
  "relationships": [
    {{"source_name": "Ama", "target_name": "Kofi", "relationship_type": "is_friends_with", "natural_language": "are mutual friends", "reasoning": "The note states 'Ama and Kofi are friends'."}},
    {{"source_name": "Ama", "target_name": "Primary School", "relationship_type": "attends", "natural_language": "attends school", "reasoning": "The note says 'Ama is a girl in primary school'."}},
    {{"source_name": "Ama", "target_name": "Neighborhood", "relationship_type": "lives_in", "natural_language": "lives in the neighborhood", "reasoning": "Implied by Kofi playing 'in the neighborhood' and both sharing the same area."}},
    {{"source_name": "Kofi", "target_name": "Neighborhood", "relationship_type": "lives_and_plays_in", "natural_language": "lives and plays in the neighborhood", "reasoning": "The note says 'Kofi is a boy who plays in the neighborhood'."}},
    {{"source_name": "Ama", "target_name": "Weekend", "relationship_type": "plays_during", "natural_language": "plays with Kofi during the weekend", "reasoning": "The note says 'Ama likes to play with Kofi every weekend'."}},
    {{"source_name": "Kofi", "target_name": "Weekend", "relationship_type": "plays_during", "natural_language": "plays with Ama during the weekend", "reasoning": "The note says 'Ama likes to play with Kofi every weekend', making it mutual."}},
    {{"source_name": "Ama", "target_name": "Homework", "relationship_type": "completes_before_play", "natural_language": "completes homework before weekend play", "reasoning": "The note says 'after she is done with her homework'."}},
    {{"source_name": "Homework", "target_name": "Weekend", "relationship_type": "precondition_for", "natural_language": "must be completed before weekend play begins", "reasoning": "The note says Ama plays 'after she is done with her homework', making homework a precondition to weekend play."}}
  ]
}}

---

Now apply this entire process to the following note and return only the JSON output, nothing else:

{extraction_content}
"""


# Unified file link parsing (vault-files + optional remote http(s)):
#   [📎 Filename](/vault-files/...) · [🎤 Voice Recording](...) · ![alt](...)
#
# URLs may contain unencoded spaces and commas (common for uploaded filenames),
# so the pattern does not stop at whitespace. They may also contain *balanced*
# parentheses — "Report (2026).pdf" is a legal markdown link target. Stopping at
# the first ")" truncated the URL, so the attachment was silently dropped: the
# file never reached PDF/image extraction and never rendered in the note. One
# level of nesting covers real filenames.
_ATTACHMENT_URL = r"(?:https?://|/vault-files/)(?:[^()\n]|\([^()\n]*\))+"
ATTACHMENT_LINK_RE = re.compile(rf"\[(📎|🎤)\s*(.*?)\]\(({_ATTACHMENT_URL})\)")
IMAGE_LINK_RE = re.compile(rf"!\[([^\]]*)\]\(({_ATTACHMENT_URL})\)")

_MAX_EXTRACTION_ATTEMPTS = 3
_MAX_SPLIT_DEPTH = 3


async def _extract_chunk(llm, text: str, count_tokens, depth: int = 0) -> Extraction:
    """Extract one chunk. Truncated output → split in half and merge, so a
    long note never yields a silently repaired, half-empty graph."""
    last_error: Exception | None = None
    for attempt in range(_MAX_EXTRACTION_ATTEMPTS):
        try:
            # Free-form generate + JSON clean (not grammar-constrained sampling),
            # which small models often empty out for nested relationship arrays.
            raw, meta = await llm.ingestion_generate_with_meta(
                _build_extraction_prompt(text), temperature=0.1
            )
            if meta.get("truncated"):
                tokens = count_tokens(text)
                if depth < _MAX_SPLIT_DEPTH and tokens > MIN_SPLIT_TOKENS:
                    logger.warning(
                        f"Extraction output truncated for a ~{tokens}-token chunk — "
                        "splitting in half and re-extracting"
                    )
                    halves = split_for_extraction(
                        text, max(MIN_SPLIT_TOKENS, tokens // 2), count_tokens
                    )
                    if len(halves) > 1:
                        parts = [
                            await _extract_chunk(llm, half, count_tokens, depth + 1)
                            for half in halves
                        ]
                        return merge_extractions(parts)
                logger.warning(
                    "Extraction output truncated on a chunk too small to split — "
                    "repairing the partial JSON"
                )
            return Extraction.model_validate_json(llm._clean_json(raw))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_error = exc
            if attempt < _MAX_EXTRACTION_ATTEMPTS - 1:
                # In-process GGUF chat can return empty/invalid JSON under KV-cache
                # pressure; a pause lets Metal/CPU recover before retrying.
                wait = 30 * (attempt + 1)
                logger.warning(
                    f"Extraction attempt {attempt + 1} failed, retrying in {wait}s: {exc}"
                )
                await asyncio.sleep(wait)
    raise RuntimeError(
        f"Extraction failed after {_MAX_EXTRACTION_ATTEMPTS} attempts: {last_error}"
    )


async def _extract_with_chunking(llm, content: str, logs: list[str]) -> tuple[Extraction, int]:
    """Split a long note into context-sized chunks, extract each, merge. Returns
    ``(extraction, chunk_count)``."""
    count = llm.ingestion_count_tokens
    overhead = count(_build_extraction_prompt(""))
    budget = chunk_token_budget(llm.ingestion_context_tokens(), overhead)
    chunks = split_for_extraction(content, budget, count) or [content]
    if len(chunks) > 1:
        note = (
            f"[Extraction] Note is ~{count(content)} tokens — extracting in "
            f"{len(chunks)} chunks of ≤{budget} tokens"
        )
        logger.info(note)
        logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {note}")
    parts: list[Extraction] = []
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            logger.info(f"[Extraction] chunk {i}/{len(chunks)} (~{count(chunk)} tokens)")
        parts.append(await _extract_chunk(llm, chunk, count))
    return (parts[0] if len(parts) == 1 else merge_extractions(parts)), len(chunks)


async def _batch_image_titles(llm, items: list[dict[str, str]]) -> dict[str, str]:
    """Name every image described this run in one call. ``{}`` on any failure —
    callers fall back to filenames, which is the accepted worst case."""
    import json as _json

    lines = "\n".join(
        f'{i + 1}. (file: {it["filename"]}) "{(it["description"] or "")[:600]}"'
        for i, it in enumerate(items)
    )
    prompt = (
        "For each numbered image description below, give a concise, specific title "
        "(2–6 words) that would serve as a unique entity name.\n\n"
        f"{lines}\n\n"
        'Return ONLY a JSON array: [{"index": 1, "title": "..."}, ...]'
    )
    try:
        raw = await llm.ingestion_generate(prompt, temperature=0.0)
        match = re.search(r"\[.*\]", raw or "", re.DOTALL)
        if not match:
            return {}
        out: dict[str, str] = {}
        for entry in _json.loads(match.group()):
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            title = str(entry.get("title") or "").strip().strip('"').strip("'")
            if isinstance(idx, int) and 1 <= idx <= len(items) and 1 < len(title) <= 80:
                out[items[idx - 1]["token"]] = title
        return out
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(f"[Images] Batch titling failed — using filenames: {exc}")
        return {}


# 1. Define Agent State
class IngestionState(TypedDict):
    """LangGraph state dictionary for the ingestion agent pipeline."""

    input: NoteInput
    content: str
    extraction: Optional[Extraction]
    note_id: Optional[str]
    created_at: Optional[str]
    errors: List[str]
    status: str  # START, MULTIMEDIA_DONE, EXTRACTED, INDEXED
    logs: List[str]
    workflow: Optional[Any]  # required KB-scoped IngestionWorkflow
    timings: dict  # per-stage seconds, reported by IngestionWorkflow._log_timing


# 2. Node Functions
async def multimodal_node(
    state: IngestionState,
):  # pylint: disable=too-many-locals,too-many-statements
    """LangGraph node: extract text from any multimedia attachments in the note."""
    _wf = _require_workflow(state)

    async def _set_status(stage: str, model: str | None = None) -> None:
        if state.get("note_id"):
            await _wf._update_note_processing_status(state["note_id"], stage, model)

    logs = state.get("logs", [])
    logs.append(
        f"[{datetime.now().strftime('%H:%M:%S')}] START: Processing Multimedia..."
    )
    import time

    t_start = time.perf_counter()
    logger.info("Processing Multimedia Sources...")
    await _set_status("Preparing multimedia attachments")

    # This gate limits concurrent multimedia processing calls.
    async with multimedia_concurrency_limit:
        logger.info(
            f"Multimedia semaphore acquired. (Active: {settings.MULTIMEDIA_CONCURRENCY - multimedia_concurrency_limit._value if hasattr(multimedia_concurrency_limit, '_value') else '?'})"  # pylint: disable=line-too-long
        )
        original_content = state["input"].content or ""
        # Re-ingest safety: drop prior extraction/transcript appendages so we
        # re-run multimedia once and rewrite a single clean enrichment section.
        content = _strip_prior_multimedia_enrichment(original_content)
        content_changed = content.strip() != original_content.strip()
        media_errors: list[str] = []
        # Images are titled in ONE batched LLM call after every multimodal
        # phase has finished. Calling the chat GGUF per image inside the
        # Florence phase evicted Florence each time (exclusive residency) and
        # cost a full multi-GB reload per image.
        pending_image_titles: list[dict[str, str]] = []
        _llm = _wf._llm

        import os
        from urllib.parse import unquote

        attachments: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        def _add_attachment(emoji: str, filename: str, url: str) -> None:
            cleaned_url = (url or "").strip()
            key = unquote(cleaned_url.split("?", 1)[0]).lower()
            if not key or key in seen_urls:
                return
            seen_urls.add(key)
            display_name = (filename or "").strip() or os.path.basename(
                unquote(cleaned_url.split("?", 1)[0])
            )
            attachments.append(
                {
                    "emoji": emoji,
                    "filename": display_name,
                    "url": cleaned_url,
                    "lower_url": key,
                }
            )

        for emoji, filename, url in ATTACHMENT_LINK_RE.findall(content):
            _add_attachment(emoji, filename, url)
        for alt, url in IMAGE_LINK_RE.findall(content):
            _add_attachment("📎", alt, url)

        video_exts = (".mp4", ".mov", ".webm", ".mkv", ".avi")
        audio_exts = (".m4a", ".mp3", ".wav", ".ogg", ".aac")
        image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
        spreadsheet_exts = (".xlsx", ".xls", ".csv", ".tsv")

        def _append(section: str) -> None:
            nonlocal content, content_changed
            content += section
            content_changed = True

        async def _run_phase(
            phase_name: str,
            model_name: str | None,
            phase_attachments: list[dict[str, str]],
            handler,
        ) -> None:
            if not phase_attachments:
                return
            await _set_status(phase_name, model_name)
            for item in phase_attachments:
                filename = item["filename"]
                url = item["url"]
                logger.info(f"[{phase_name}] Processing File: {filename} ({url})")
                try:
                    section = await handler(item)
                    if section:
                        _append(section)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error(f"[{phase_name}] File Processing Failed: {e}")
                    media_errors.append(f"{filename}: {e}")

        async def _handle_pdf(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected PDF. Extracting text with Florence visual pass...")
            loop = asyncio.get_running_loop()

            def _progress(stage: str, model: str | None = None) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    _set_status(stage, model), loop
                )
                future.result(timeout=30)

            pdf_text = await asyncio.to_thread(
                multimedia_service.extract_text_from_pdf, item["url"], _progress
            )
            snippet = pdf_text.replace("\n", " ")
            logger.info(f'PDF Result ({len(pdf_text)} chars): "{snippet[:100]}"')
            return f"\n\n[PDF Extraction ({filename})]: {pdf_text}"

        async def _handle_image(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected Image. Describing with Florence...")
            img_desc = await asyncio.to_thread(
                multimedia_service.describe_image, item["url"]
            )
            logger.info(f'Image Description: "{img_desc}"')

            # Placeholder now; the real title is filled in by the batched
            # titling pass below (filename if that pass fails).
            token = f"{{{{ORB_IMAGE_TITLE_{len(pending_image_titles)}}}}}"
            pending_image_titles.append(
                {"token": token, "filename": filename, "description": img_desc}
            )
            return (
                f"\n\n[Image: {token}]\n"
                f'The image titled "{token}" shows the following: {img_desc}'
            )

        async def _handle_docx(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected Word document. Extracting text...")
            doc_text = await asyncio.to_thread(
                multimedia_service.extract_text_from_docx, item["url"]
            )
            snippet = doc_text.replace("\n", " ")
            logger.info(f'Word Result ({len(doc_text)} chars): "{snippet[:100]}"')
            return f"\n\n[Word Extraction ({filename})]: {doc_text}"

        async def _handle_spreadsheet(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected spreadsheet. Extracting text...")
            sheet_text = await asyncio.to_thread(
                multimedia_service.extract_text_from_spreadsheet, item["url"]
            )
            snippet = sheet_text.replace("\n", " ")
            logger.info(
                f'Spreadsheet Result ({len(sheet_text)} chars): "{snippet[:100]}"'
            )
            return f"\n\n[Spreadsheet Extraction ({filename})]: {sheet_text}"

        async def _handle_audio(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected Audio. Transcribing with Whisper...")
            transcription = await asyncio.to_thread(
                multimedia_service.transcribe_audio, item["url"]
            )
            snippet = transcription.replace("\n", " ")
            logger.info(f'Audio Result ({len(transcription)} chars): "{snippet[:100]}"')
            return f"\n\n[Audio Transcript ({filename})]: {transcription}"

        async def _handle_video_audio(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected Video. Transcribing audio with Whisper...")
            transcription = await asyncio.to_thread(
                multimedia_service.transcribe_video_audio, item["url"]
            )
            if not transcription:
                return ""
            snippet = transcription.replace("\n", " ")
            logger.info(
                f'Video Audio Result ({len(transcription)} chars): "{snippet[:100]}"'
            )
            return f"\n\n[Video Audio Transcript ({filename})]:\n\n{transcription}"

        async def _handle_video_visual(item: dict[str, str]) -> str:
            filename = item["filename"]
            logger.info("Detected Video. Running Marlin visual analysis...")
            visual_text = await asyncio.to_thread(
                multimedia_service.describe_video_visual, item["url"]
            )
            if not visual_text:
                return ""
            snippet = visual_text.replace("\n", " ")
            logger.info(f'Video Visual Result: "{snippet[:100]}"')
            return f"\n\n[Video Visual Analysis ({filename})]:\n\n{visual_text}"

        pdfs = [item for item in attachments if item["lower_url"].endswith(".pdf")]
        images = [
            item for item in attachments if item["lower_url"].endswith(image_exts)
        ]
        docx_files = [
            item for item in attachments if item["lower_url"].endswith(".docx")
        ]
        spreadsheets = [
            item for item in attachments if item["lower_url"].endswith(spreadsheet_exts)
        ]
        audio_files = [
            item
            for item in attachments
            if (
                item["lower_url"].endswith(audio_exts) or item["emoji"] == "🎤"
            )
            and not item["lower_url"].endswith(video_exts)
        ]
        # Classify by extension — do not require emoji 📎 (same marker as images).
        videos = [
            item
            for item in attachments
            if item["lower_url"].endswith(video_exts)
        ]

        # Phase 1: finish all Florence-backed work, then release Florence.
        await _run_phase("Reading PDF pages and images", "Florence-2", pdfs, _handle_pdf)
        await _run_phase("Describing images", "Florence-2", images, _handle_image)
        if pdfs or images:
            await _set_status("Unloading image model", "Florence-2")
            await asyncio.to_thread(multimedia_service.unload_local_models, "florence")

        # Phase 2: lightweight document extraction that does not hold ML models.
        await _run_phase("Extracting documents", None, docx_files, _handle_docx)
        await _run_phase("Extracting spreadsheets", None, spreadsheets, _handle_spreadsheet)

        # Phase 3: finish all Whisper work, then release Whisper.
        await _run_phase("Transcribing audio", "Whisper", audio_files, _handle_audio)
        await _run_phase("Transcribing video audio", "Whisper", videos, _handle_video_audio)
        if audio_files or videos:
            await _set_status("Unloading speech model", "Whisper")
            await asyncio.to_thread(multimedia_service.unload_local_models, "whisper")

        # Phase 4: run Marlin only after local-models no longer holds Whisper.
        await _run_phase("Analyzing video visuals", "Marlin", videos, _handle_video_visual)
        if videos:
            await _set_status("Unloading video model", "Marlin")
            await asyncio.to_thread(multimedia_service.unload_marlin)

        supported_ids = {
            id(item)
            for item in pdfs + images + docx_files + spreadsheets + audio_files + videos
        }
        for item in attachments:
            if id(item) not in supported_ids:
                logger.info(f"Skipped (Unsupported Type): {item['url']}")

        # Title images in one call now that no multimodal model is resident —
        # the chat GGUF this loads is the same one extraction needs next.
        if pending_image_titles:
            await _set_status("Naming images", _llm.get_ingestion_model() or "LLM")
            titles = await _batch_image_titles(_llm, pending_image_titles)
            for item in pending_image_titles:
                content = content.replace(
                    item["token"], titles.get(item["token"]) or item["filename"]
                )

        # Sync enriched content back to the vault .md (source of truth).
        if media_errors:
            raise RuntimeError(
                "Multimedia processing failed for: " + "; ".join(media_errors)
            )

        if content_changed and content.strip() != original_content.strip():
            if state.get("note_id"):
                logger.info(
                    f"Syncing processed multimedia content to vault for Note {state['note_id']}..."
                )
                await _set_status("Saving extracted attachment text")
                await _wf._persist_note_body(state["note_id"], content)

    t_end = time.perf_counter()
    logger.info(f"Multimedia processing took: {t_end - t_start:.4f}s")
    return {
        "content": content.strip(),
        "logs": logs,
        "status": "MULTIMEDIA_DONE",
        "timings": {**(state.get("timings") or {}), "multimedia": round(t_end - t_start, 2)},
    }


async def extraction_node(
    state: IngestionState,
):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """LangGraph node: run structured LLM extraction to produce an Extraction object."""
    _wf = _require_workflow(state)
    _llm = _wf._llm
    if state.get("note_id"):
        await _wf._update_note_processing_status(
            state["note_id"],
            "Extracting knowledge graph",
            _llm.get_ingestion_model() or "LLM",
        )

    logs = state["logs"]
    logs.append(
        f"[{datetime.now().strftime('%H:%M:%S')}] EXTRACT: Running Knowledge Architect "
        f"({_llm.get_ingestion_model() or _llm.provider})..."
    )
    import time

    t_start = time.perf_counter()
    logger.info("Extracting Metadata (Knowledge Architect)...")

    # Prepend the user-provided title so the LLM sees it as part of the source
    # text. Auto-generated titles are not included — they don't originate from
    # the note itself and would pollute the extraction.
    extraction_content = state["content"]
    if state["input"].title:
        extraction_content = f"# {state['input'].title}\n\n{extraction_content}"

    try:
        extraction, n_chunks = await _extract_with_chunking(_llm, extraction_content, logs)
    except Exception as _e:  # pylint: disable=broad-exception-caught
        logger.error(f"Extraction Error: {_e}")
        return {"errors": [str(_e)], "logs": logs}

    logger.info(f"Extraction Completed: {extraction}")

    # Log per-entity type reasoning for auditability
    for n in extraction.nodes:
        reasoning = (
            n.type_reasoning.strip() if n.type_reasoning else "no reasoning provided"
        )
        logger.info(
            f"  [Entity] name={n.name!r} type={n.type!r} reasoning={reasoning!r}"
        )

    # Log per-relationship reasoning for auditability
    for r in extraction.relationships:
        rel_reasoning = r.reasoning.strip() if r.reasoning else "no reasoning provided"
        logger.info(
            f"  [Relationship] {r.source_name!r} -> {r.target_name!r}"
            f" ({r.relationship_type!r}): {rel_reasoning!r}"
        )

    # GARBAGE NAME HANDLING: nodes with empty/placeholder names but valid context
    # get a recovery rename; nodes with neither are dropped.
    GARBAGE_NAMES = {"untitled", "none", "unknown", ""}  # pylint: disable=invalid-name

    clean_nodes = []
    renameable = []
    for n in extraction.nodes:
        val = (n.name or "").strip().lower()
        if val not in GARBAGE_NAMES:
            clean_nodes.append(n)
        elif n.isolated_context:
            renameable.append(n)
        # else: no name and no context — silently drop

    if renameable:
        import json as _json
        import re as _re

        batch_lines = "\n".join(
            f'{i+1}. (type={n.type}) "{n.isolated_context}"'
            for i, n in enumerate(renameable)
        )
        rename_prompt = (
            "For each numbered excerpt below, provide the most specific descriptive name "
            "for the node it describes (1–5 words each).\n\n"
            "Return null if an excerpt has insufficient information to name specifically.\n\n"
            "Return ONLY a JSON array: "
            '[{"index": 1, "name": "Name Here"}, {"index": 2, "name": null}, ...]\n\n'
            f"{batch_lines}\n\n"
            'Return ONLY: [{"index": 1, "name": "..."}, ...]'
        )
        try:
            rename_resp = await _llm.ingestion_generate(
                rename_prompt,
                temperature=0.0,
            )
            match = _re.search(r"\[.*?\]", rename_resp, _re.DOTALL)
            if match:
                name_list = _json.loads(match.group())
                name_map = {
                    e["index"]: e.get("name")
                    for e in name_list
                    if isinstance(e, dict) and "index" in e
                }
                for i, node in enumerate(renameable):
                    new_name = name_map.get(i + 1)
                    if (
                        isinstance(new_name, str)
                        and new_name.strip()
                        and new_name.strip().lower() not in GARBAGE_NAMES
                        and 2 < len(new_name.strip()) <= 80
                    ):
                        node.name = new_name.strip()
                        logger.info(f"  [Rename] Recovered → '{node.name}'")
                        clean_nodes.append(node)
                    else:
                        logger.info(
                            f"  [Rename] Could not recover (response: '{new_name}')"
                        )
            else:
                logger.warning("  [Rename] Could not parse batch rename response.")
        except Exception as rename_err:  # pylint: disable=broad-exception-caught
            logger.warning(f"  [Rename] Batch rename failed: {rename_err}")

    # Dedup: if recovered names collide with existing clean nodes or with
    # each other, keep only the first occurrence by normalized name.
    _seen_names: set[str] = set()
    deduped_nodes = []
    for n in clean_nodes:
        _key = (n.name or "").strip().lower()
        if _key and _key not in _seen_names:
            _seen_names.add(_key)
            deduped_nodes.append(n)
        elif _key:
            logger.debug(f"  [Rename] Dedup: dropped duplicate node name '{n.name}'")
    extraction.nodes = deduped_nodes

    logger.info(f"Nodes after rename: {[n.name for n in extraction.nodes]}")

    logger.info(
        f"Extracted: {len(extraction.nodes)} nodes, {len(extraction.relationships)} relationships."
    )

    t_end = time.perf_counter()
    logger.info(f"Extraction took: {t_end - t_start:.4f}s")
    return {
        "extraction": extraction,
        "logs": logs,
        "status": "EXTRACTED",
        "timings": {
            **(state.get("timings") or {}),
            "extraction": round(t_end - t_start, 2),
            "extraction_chunks": n_chunks,
        },
    }


async def storage_node(state: IngestionState):
    """LangGraph node: persist the validated extraction to the graph and vector stores."""
    if state.get("errors") or not state.get("extraction"):
        return {"errors": state.get("errors") or ["Missing extraction data"]}
    _wf = _require_workflow(state)
    if state.get("note_id"):
        await _wf._update_note_processing_status(
            state["note_id"], "Writing graph and note metadata", None
        )

    logs = state["logs"]
    logs.append(
        f"[{datetime.now().strftime('%H:%M:%S')}] STORE: Writing to Graph & metadata..."
    )

    import time

    t_start = time.perf_counter()
    note_id = state.get("note_id") or str(uuid.uuid4())
    created_at = state.get("input").created_at or datetime.now().isoformat()
    custom_title = state.get("input").title  # May be None

    try:
        # 1. Write ontology to Kuzu / Qdrant / Meili
        title = await asyncio.to_thread(
            _wf._write_ontology,
            note_id,
            state["content"],
            state["extraction"],
            created_at,
            custom_title,  # Pass custom title if provided
        )

        # 2. Sync title to SQLite metadata (body stays in vault .md)
        if title:
            await _wf._update_note_title(note_id, title)

        t_end = time.perf_counter()
        logger.info(f"  [Perf] Graph Storage took: {t_end - t_start:.4f}s")
        return {
            "note_id": note_id,
            "created_at": created_at,
            "timings": {**(state.get("timings") or {}), "storage": round(t_end - t_start, 2)},
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logs.append(f"ERROR: Storage failed: {e}")
        return {"errors": [f"Storage failed: {str(e)}"], "logs": logs}


async def summarization_node(state: IngestionState):
    """LangGraph node: update per-node context summaries and mark ingestion complete."""
    if state.get("errors") or not state.get("extraction"):
        return {}
    _wf = _require_workflow(state)
    if state.get("note_id"):
        await _wf._update_note_processing_status(
            state["note_id"], "Indexing entity contexts", "Embeddings"
        )
    logs = state["logs"]
    logs.append(
        f"[{datetime.now().strftime('%H:%M:%S')}] INDEX_CONTEXTS: Updating Node Contexts..."
    )
    import time

    t_start = time.perf_counter()
    logger.info("[Agent] Updating Node Context Indexes (Delta Updates)...")
    try:
        await _wf._update_neighborhoods(
            state["extraction"].nodes,
            state["content"],
            note_created_at=state.get("created_at"),
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(f"[Agent] Context indexing failed: {e}", exc_info=True)
        logs.append(
            f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: Context indexing failed: {e}"
        )
        return {
            "logs": logs,
            "errors": [f"Context indexing failed: {e}"],
            "status": "FAILED",
        }
    t_end = time.perf_counter()
    logger.info(f"  [Perf] Context indexing took: {t_end - t_start:.4f}s")

    logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] DONE: Ingestion Complete.")
    return {
        "logs": logs,
        "status": "INDEXED",
        "timings": {**(state.get("timings") or {}), "indexing": round(t_end - t_start, 2)},
    }


def should_route_after_extraction(state: IngestionState):
    """Route the graph after extraction: 'end' on error, 'store' otherwise."""
    if state.get("errors"):
        return "end"
    return "store"


# 3. Build Graph
workflow = StateGraph(IngestionState)

# Add Nodes
workflow.add_node("multimodal", multimodal_node)
workflow.add_node("extraction", extraction_node)
workflow.add_node("storage", storage_node)
workflow.add_node("summarization", summarization_node)

# Define Edges
workflow.set_entry_point("multimodal")
workflow.add_edge("multimodal", "extraction")
workflow.add_conditional_edges(
    "extraction",
    should_route_after_extraction,
    {"store": "storage", "end": END},
)

workflow.add_edge("storage", "summarization")
workflow.add_edge("summarization", END)

# Compile
ingestion_agent = workflow.compile()
