# Sweep round3-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{}` (one-at-a-time)

## Rung: first 50 dev questions (2 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.683 | 54% | 0.920 | 0.990 | 119 | 4656 (486/492 notes) | 8 {'query analysis': 7, 'research step': 1} |  |
| ingestion_model=qwen35-9b-q4,EXTRACTION_ENABLE_THINKING=False | 0.555 | 46% | 0.760 | 0.840 | 95 | 25442 (399/492 notes) | 8 {'query analysis': 7, 'research step': 1} | +4/-8 EM, answer_f1 CI -0.259 to -0.002, within noise |
