# Plan: better transcripts and lecture notes from audio attachments

For a coding agent picking up work on Orb. Written 2026-09-21 against Orb main at `3bd480f`.
**Phase 1 (§3) is implemented** — `restore_punctuation` and GPU diarization in `asr_engine.py`, tests in `test_asr_engine.py`. Phase 2 (§4) is not started: it waits on the owner's answers to §5. Every claim about Orb names the file it lives in; verify before
changing behaviour. Read `Docs/HANDOFF-local-llm-and-context.md` and
`Docs/11-multimedia-enrichment.md` first.

## 1. Where this comes from

Orb's transcriber (`backend/app/services/asr_engine.py`) is a port of the sibling project
`local-transcription-service` (`~/Projects/Technical/personal/local-transcription-service`). On
2026-09-21 that project was benchmarked against Wispr Flow transcripts of the same lectures, three
bugs were found and fixed there, and it gained a notes-style output. The fixes are commits
`42f7559`, `4f073b6`, `8190401`; the notes feature is `cbc9a73` and `a400bdd`. Orb still has two of
the three bugs and none of the notes output.

What was measured there, on an M3 with 24 GB, torch 2.13, pyannote 4.0.7:

| Finding | Number |
|---|---|
| pyannote community-1 on `mps` vs `cpu`, 10-minute lecture slice, step 2.0 | 40 s vs 284 s, identical turns (64 turns, 2 speakers, 100% frame agreement) |
| Aligner words vs text tokens over an 86-minute recording | 10,749 vs 10,735, so the all-or-nothing punctuation restore dropped every full stop in the file |
| Qwen3-ASR 1.7B word agreement with Wispr Flow on a far-field lecture slice | about 81%; Whisper large-v3-turbo 78.7% and it looped; loudness normalization and context terms did not move it |
| Language left on auto-detect | Hindi and Chinese sentences inside English lectures. Orb already pins `ASR_LANGUAGE = "en"` (`core/config.py`), so Orb does not have this bug |
| 86-minute class as tokens (Gemma 4 tokenizer) | 13,648 plain text; 15,689 as speaker paragraphs; 23,717 as `[MM:SS] Speaker N:` lines |
| Gemma 4 E4B Q4_K_M, 32k context, title + structured summary of that class | one call, fits with room; diarize + summarize took 519 s together |

## 2. What Orb does today

- `multimodal_runtime.transcribe_audio_path` transcribes, and when the aligner and diarizer are
  downloaded, returns `asr_engine.label_speakers(...)`: `Speaker 1: …` paragraphs, one per run of a
  speaker, no timestamps.
- `ingestion_agent.extract_attachment` wraps that as `[Audio Transcript (<filename>)]: <text>`, and
  `place_extraction` writes it into the note as an `orb:extract` block.
- `resolve_large_attachment` parks the block as `mode="pending"` when it exceeds
  `LARGE_ATTACHMENT_TOKENS` (20000). On *Summarize instead* it calls
  `LLMService.summarize_document`, which writes generic prose ("key concepts, named people…") with a
  piecewise-then-merge fallback.

## 3. Phase 1: port the two bug fixes

No product decision in this phase. Both are changes to `backend/app/services/asr_engine.py`.

### 3.1 Diarize on the GPU

`speaker_turns` calls `pipeline.to(torch.device("cpu"))`. The comment in `core/config.py` above
`ASR_SPEAKERS` and §5.3 of `Docs/11-multimedia-enrichment.md` both say "on the CPU".

- Pick the device in order `mps`, `cuda`, `cpu`, the same order the sibling uses
  (`diarize.py`, commit `42f7559`). `MultimodalRuntime.device` in `multimodal_runtime.py` already
  answers this question for the other models; reuse it if its answer is the same, otherwise keep the
  three-way check local to `speaker_turns`.
- Residency: on the MLX path the ASR weights are already released before `_speaker_turns` runs
  (`transcribe_with_mlx` clears the MLX cache in its `finally`). On the transformers path
  `_speaker_turns` runs inside the lock while the ASR and aligner torch models are still loaded
  (`_transcribe_with_transformers`). On a CUDA card that is three models resident at once. Check
  memory there before shipping; if it does not fit, unload ASR and aligner before diarizing. That
  path was not measured in the sibling project, which only has the MLX engine.
- After `del pipeline`, also empty the `mps`/`cuda` cache. The sibling's
  `memory.release_accelerator_memory` does this; Orb's `local_models.release_accelerator_memory`
  is the equivalent.
- The step figures in the `ASR_SPEAKERS` comment (1.8x, 3.4x realtime) were measured on CPU. Keep
  the agreement figures, which do not depend on the device, and restate the speeds as CPU figures.

### 3.2 Keep punctuation when word counts differ

`transcribe_with_mlx` restores punctuation only when `len(tokens) == len(words)` over the whole
recording. `align_with_transformers` has the same check per 30-second chunk. When it fails,
`label_speakers` rebuilds the note's transcript from bare words, so a long lecture enters the vault,
and then entity extraction, with no punctuation at all. On the MLX path the comparison spans the
whole recording, so on long audio it should be expected to fail.

- Replace both checks with one helper that matches the two sequences and restores punctuation where
  they agree. The sibling's version is `_restore_punctuation` in `whisper_engine.py` (commit
  `4f073b6`): `difflib.SequenceMatcher` over alphanumeric-only lowercase forms, `autojunk=False`,
  then copy the punctuated token onto each matched word. It took under a second on 11,000 words.
- This is a fix to the writer, which is what `HANDOFF` §7 asks for. Existing notes keep their
  unpunctuated transcripts until they are re-ingested. A migration is not possible from the vault
  alone: unlike the sibling's `.json`, the note does not keep a punctuated copy of the text beside
  the bare words. Say so in the release notes; do not add a normaliser.
- Test: add a case to `backend/tests/unit/test_asr_engine.py` where the aligner returns one word
  more than the text has tokens, and assert the punctuation on both sides of the extra word survives.

### 3.3 Verify

`pytest tests/unit/test_asr_engine.py`, then ingest a note with a 10-minute two-speaker recording
and compare the block before and after: same speaker turns, punctuation present, diarization time in
`DATA_DIR/logs` down by roughly the factor in §1.

## 4. Phase 2: lecture notes for audio

The sibling's `--summarize` output is the target shape: a title, a one-paragraph flow summary, three
to five topic sections of short bullets, *Next Steps* attributed to `(Speaker N)`, and *Decisions
Made*. The prompt and its rules are `SYSTEM`, `SHAPE` and `RULES` in the sibling's `summarize.py`.
The rules that matter: use only what the transcript says; copy numbers and names exactly; the input
is speech recognition and mishears words, so drop what makes no sense rather than guess.

Orb already has everything needed to run it. Do not copy the sibling's llama.cpp loading code: route
the call through `LLMService` so cloud workspaces and the ingestion-model override keep working, the
per-call output budget applies, and `PromptTooLongError` stays loud.

- Add `LLMService.summarize_transcript(filename, text)` beside `summarize_document` in
  `services/llm.py`. Same piecewise structure: when the transcript fits
  `ingestion_context_tokens()` with room for the answer, one call with the notes prompt; otherwise
  per-part bullet notes (the sibling's `PART_SYSTEM`), then the notes prompt over those. Plain
  markdown out, no `json_mode`.
- In `resolve_large_attachment`, the `summary` branch calls `summarize_transcript` when the section
  is an audio or video-audio transcript and `summarize_document` otherwise. The cleanest signal is
  the attachment `kind` that `extract_attachment` already has; thread it through rather than
  matching on the `[Audio Transcript (` marker text.
- The block stays `mode="summary"` with body `[Summary (<filename>)]: …`, so `graph_text`, the strip
  on re-ingest and the editor's marker handling need no change.

This is the smallest version: it only changes what *Summarize instead* produces for audio. It does
nothing for a recording under 20,000 tokens, which is graphed in full with no summary. Whether that
is enough is decision 5.1.

## 5. Decisions for the owner

These change product behaviour, so they are not the implementing agent's to make.

1. **When does an audio attachment get notes?** Options: only through *Summarize instead* on a large
   attachment (§4 as written); always, as a second block under the transcript; or a per-note action
   in the editor. "Always" adds one LLM call per recording to every ingest, on a machine where one
   heavy model is resident at a time, so it also adds a model swap.
2. **Summary and transcript, or summary instead of transcript?** Today `mode="summary"` replaces the
   text in the block. For a lecture the transcript is the thing the user searches. Keeping both means
   a summary block that is graphed plus the transcript as `mode="index"`, which is two blocks for one
   attachment; `extraction_blocks` and `set_block_mode` key blocks by `src`, so that needs a design,
   not a patch.
3. **Timestamps in the transcript.** `[MM:SS] Speaker N:` lines make a transcript navigable, and the
   sibling writes them. In Orb they cost 51% more tokens than speaker paragraphs for the same class
   (23,717 vs 15,689), which pushes an 86-minute lecture over `LARGE_ATTACHMENT_TOKENS` and puts a
   timestamp in front of every sentence that entity extraction reads. Only worth it if the note page
   can seek the audio from a timestamp; check whether it can before deciding.
4. **A glossary pass.** The benchmark's remaining errors are misheard technical terms ("ethical
   hacking" as "physical helping", a cafeteria named KONO as "corner"). A workspace's existing
   entity names are exactly the glossary a correction pass would need, and Orb has them. This is
   untested anywhere. If wanted, prototype it in the sibling project first, against the same Wispr
   reference, before it touches ingestion.

## 6. Not doing

- Switching the ASR model. Whisper large-v3-turbo scored lower and looped on the same audio; the
  full large-v3 was already rejected in `asr_engine.py`'s header for dropped speech.
- Loudness normalization before transcription, or passing context terms to Qwen. Both were measured
  and neither moved word agreement beyond run-to-run noise (about 3 points).
- Any change to `ASR_LANGUAGE`. Orb already defaults to `en`.
- New environment variables. Any new knob is a `Settings` field with a `LOCAL_RUNTIME_KEYS` entry and
  a row in `LocalRuntimeCard.tsx`, per `HANDOFF` §2. Phase 1 needs none.

## 7. Docs to update with the code

`Docs/11-multimedia-enrichment.md` §5.3 (device, punctuation) and §6.4 (the summary branch);
`Docs/12-local-models-and-inference.md` where it describes diarizer residency;
`Docs/21-configuration-reference.md` for the `ASR_SPEAKERS` comment; the comment block above
`ASR_SPEAKERS` in `core/config.py`.

## 8. Verification

From `backend/` with `DATA_DIR` on a scratch folder: `pytest tests/unit`, `pytest tests/integration`.
For Phase 2 add unit tests beside `tests/unit/test_large_attachments.py`: an audio section over the
threshold with decision `summary` calls `summarize_transcript`, a PDF section still calls
`summarize_document`. Then the app loop in `HANDOFF` §7, and one real ingest of a lecture recording,
reading the summary against the transcript for invented figures or names.
