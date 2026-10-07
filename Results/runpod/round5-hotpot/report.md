# Sweep round5-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"EXTRACTION_PROMPT_SUFFIX": ["Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one."]}` (one-at-a-time)

## Rung: first 50 dev questions (3 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.558 | 46% | 0.920 | 0.980 | 372 | 6599 (483/492 notes) | 8 {'query analysis': 5, 'research step': 3} |  |
| EXTRACTION_PROMPT_SUFFIX=#e1451c73 | 0.520 | 42% | 0.930 | 0.990 | 465 | 6869 (484/492 notes) | 11 {'query analysis': 7, 'research step': 4} | +7/-9 EM, answer_f1 CI -0.187 to +0.119, within noise |
| chat_model=qwen35-9b-q4,ENABLE_THINKING=False | 0.614 | 48% | 0.940 | 0.980 | 369 | 6599 (483/492 notes) | 4 {'research step': 4} | +6/-5 EM, answer_f1 CI -0.042 to +0.161, within noise |
