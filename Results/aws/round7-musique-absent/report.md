# Sweep round7-musique-absent

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5, "INDEX_RAW_PASSAGES": true}`
Varied: `{"INDEX_RAW_PASSAGES": [false], "FIRST_SEARCH_IS_QUESTION": [true]}` (one-at-a-time)

## Rung: first 50 dev questions (3 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.299 | 24% | 0.572 | 0.578 | 451 | 17028 (410/526 notes) | 13 {'query analysis': 10, 'research step': 3} |  |
| INDEX_RAW_PASSAGES=False | 0.241 | 16% | 0.630 | 0.677 | 359 | 15073 (438/526 notes) | 14 {'query analysis': 14} | +2/-6 EM, answer_f1 CI -0.173 to +0.053, within noise |
| FIRST_SEARCH_IS_QUESTION=True | 0.281 | 22% | 0.562 | 0.578 | 439 | 17028 (410/526 notes) | 12 {'query analysis': 6, 'research step': 6} | +2/-3 EM, answer_f1 CI -0.100 to +0.063, within noise |

## Absent: questions 100 to 150, whose notes are not in the index (lower answered is better)

| variant | answered | s / question |
|---|---|---|
| base | not run | |
| INDEX_RAW_PASSAGES=False | not run | |
| FIRST_SEARCH_IS_QUESTION=True | not run | |
