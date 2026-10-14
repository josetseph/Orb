# Sweep round9-musique

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5, "INDEX_RAW_PASSAGES": true, "PASSAGE_KEYWORD_SEARCH": true, "PASSAGE_TITLE_HEADER": true, "FIRST_SEARCH_IS_QUESTION": true, "CONTEXT_ORDER": "edges"}`
Varied: `{}` (one-at-a-time)

## Rung: first 50 dev questions (3 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.342 | 24% | 0.608 | 0.635 | 281 | 14864 (405/526 notes) | 6 {'query analysis': 3, 'research step': 3} |  |
| chat_model=qwen35-9b-q4,ENABLE_THINKING=False | 0.407 | 34% | 0.608 | 0.635 | 290 | 14864 (405/526 notes) | 4 {'research step': 4} | +7/-2 EM, answer_f1 CI -0.036 to +0.166, within noise |
| chat_model=gemma4-12b-q4 | 0.414 | 34% | 0.628 | 0.635 | 366 | 14864 (405/526 notes) | 0  | +6/-1 EM, answer_f1 CI -0.022 to +0.164, within noise |
