# Sweep round9-musique-control

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5, "INDEX_RAW_PASSAGES": false, "PASSAGE_KEYWORD_SEARCH": false, "PASSAGE_TITLE_HEADER": false, "FIRST_SEARCH_IS_QUESTION": false, "CONTEXT_ORDER": "rank"}`
Varied: `{}` (one-at-a-time)

## Rung: first 50 dev questions (1 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.223 | 18% | 0.592 | 0.635 | 240 | 14864 (405/526 notes) | 15 {'query analysis': 12, 'research step': 3} |  |
