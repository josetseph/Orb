# Sweep round6-passages-musique

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5, "INDEX_RAW_PASSAGES": true}`
Varied: `{"INDEX_RAW_PASSAGES": [false]}` (one-at-a-time)

## Rung: first 50 dev questions (2 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.273 | 24% | 0.557 | 0.578 | 563 | 17028 (410/526 notes) | 17 {'research step': 8, 'query analysis': 9} |  |
| INDEX_RAW_PASSAGES=False | 0.227 | 18% | 0.640 | 0.677 | 458 | 15073 (438/526 notes) | 18 {'query analysis': 15, 'research step': 3} | +1/-4 EM, answer_f1 CI -0.131 to +0.033, within noise |
