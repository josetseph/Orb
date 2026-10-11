# Sweep round6-granite-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "granite42-8b-q4"}`
Varied: `{}` (one-at-a-time)

## Rung: first 50 dev questions (1 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.527 | 38% | 0.790 | 0.850 | 212 | 3915 (374/492 notes) | 12 {'query analysis': 9, 'research step': 3} |  |
