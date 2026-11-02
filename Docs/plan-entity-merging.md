# Plan: merge duplicate entities when they are written, not when they are searched

## Why

Retrieval used to run a "name variant" lookup on every search (`find_name_variants_batch`). For each hit, it
scanned every node for names that contained the hit's name, using text and pattern checks. It was the slowest
part of answering, and most of what it added was noise:

- On MuSiQue with Opus (50 questions, 2026-11-01), about 90% of each question's time went to the "retrieval
  phase". The time grew with the number of vector hits, at about 1 s per hit: 6 hits took 5 s, 666 hits took
  641 s.
- It added 4,094 nodes over 49 searches, almost all unrelated. "electron" pulled in "electronic pop" and "Dick
  Smith Electronics"; "proton" pulled in "proton pump inhibitors"; "2010" pulled in "september 2010". The
  reranker then had to discard them.
- It could not catch real aliases ("JFK" and "John F. Kennedy" share no text).

**The lookup has been removed** from `services/retrieval.py`, and `find_name_variants*` from `services/graph.py`
(main and orb-testing-2). Merging belongs where entities are written, plus a periodic clean-up.

## What the graph looks like today

- Ingestion links a new entity to an existing node **only on an exact normalised name match**
  (`workflows/ingestion.py`: `find_node_ids_by_names` in Qdrant, then `find_nodes_by_exact_names` in Kuzu).
- Measured on the MuSiQue index (`orb-testing-2/snapshots/idx-musique-482d75c1`): 26,690 entities. 266 groups
  (337 extra nodes, about 1.3%) differ only by "the", a plural, punctuation or a bracketed qualifier. The groups
  are a mix of the two cases below, so **no name rule can decide a merge**:
  - Real duplicates: "denver post" / "the denver post", "emmy award" / "emmy awards", "new york times" / "the
    new york times".
  - Correctly separate: "the inspector general" / "the inspector general (song)", "rolling stone" / "the rolling
    stones", "beethoven" / "beethoven (1992 film)".
- Long descriptive names such as "Black Death: The 14th-Century Bubonic Plague Pandemic" are **note titles**
  (note nodes), not duplicate entities. Leave them alone.
- Not yet measured: aliases with no shared text. Count them in step 1.

## Design

### 1. Measure first

Add a harness check (`tests/benchmark`) that reports, for a snapshot:

- the duplicate candidates from step 2's candidate search;
- the model's same-entity decisions on them;
- a hand-labelled sample of 50 pairs, to check those decisions.

Run it on the HotpotQA and MuSiQue snapshots. If real duplicates turn out to be well under 1%, do step 3 (the
gardener) before step 2, because it does not slow ingestion.

### 2. Resolve at ingestion

For each extracted entity with no exact match:

1. **Candidates.** Search the node vectors already in Qdrant for the entity's name and type, using the
   entity's own embedding, which is computed anyway. Keep the few nodes above a similarity threshold, with a
   cap such as 5. Calibrate the threshold on the step-1 labels.
2. **Decision.** Send one model call per entity that has candidates. It receives the new entity's name, type
   and context, plus each candidate's name, type and summary, and answers which candidate (if any) is the same
   real-world thing. Use typed JSON output.
   - A malformed or refused reply means "no merge": a counted failure, never repaired (26 D12).
   - Save the decision in the call memo, keyed by its inputs (`ingestion_checkpoint.memo`), so a resumed
     ingest does not ask again (26 D14).
3. **Write.** "Same" means the new mention takes the existing node's ID, through the same path an exact match
   uses today. Record the surface form as an alias on the node, so the next exact lookup matches it without a
   model call.

Cost: no call when there is an exact match or no candidate. Count the extra calls per note in the harness and
report them with every run.

### 3. Graph gardener (roadmap: Phase 3, priority Now)

A periodic agent catches what ingestion missed: aliases across notes, graphs ingested before step 2, and
pairs a later note shows to be the same.

- **Trigger:** after an ingest batch, and nightly.
- **Candidates:** the same search and decision call as step 2, run over node pairs not yet judged, oldest
  first.
- **Proposals, not silent writes** (roadmap harness rule): each merge is a proposal with a diff showing names,
  contexts and the relations that would move. Auto-apply can be enabled per KB later.
- **The merge itself** is one resumable job (`services/jobs.py`). Repoint relations, combine contexts, keep
  the aliases, delete the absorbed node from Kuzu, Qdrant and Meilisearch, and record the merge so it can be
  undone. Use deterministic IDs and delete-then-write, so a crash mid-merge leaves no orphans when it resumes.
- **Never merge** across node kinds (entity and note), or across types the model marks as distinct (a film
  and its soundtrack).

## How to prove it

- **Unit:** a fake model client with fixed replies. A pair judged "same" ends as one node, with both names as
  aliases and every relation repointed. A pair judged "different", or given a malformed reply, stays as two
  nodes. Kill-and-resume mid-merge gives the same final state (`tests/unit/test_resume.py`).
- **Harness:** re-ingest HotpotQA and MuSiQue from the LLM cache (only the new decision calls are live), then
  compare F1, EM, recall, entity count and time per question with the runs in `Results/` that still used the
  variant lookup. Report the extra decision calls and their cost.

## Order of work

1. The measurement check (step 1).
2. Ingestion-time resolution (step 2), behind a setting that is on by default, with unit tests.
3. The gardener: candidate pass, proposals table and UI, the merge job, undo.
4. Harness comparison and the results write-up.

## Don't

- Don't merge on name rules (stripping "the", plurals, brackets): the measurement shows they get it wrong in
  both directions.
- Don't add the variant lookup back to retrieval in any form.
- Don't repair or normalise the model's decision reply.
