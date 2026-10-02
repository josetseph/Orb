# Sweep round2-scale-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"ingestion_model": ["gemma4-e4b-q4"]}` (one-at-a-time)

## Rung: first 50 dev questions (2 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.670 | 58% | 0.790 | 143 | 45817 (487/492 notes) | 6 {'query analysis': 5, 'research step': 1} |  |
| ingestion_model=gemma4-e4b-q4 | 0.416 | 32% | 0.430 | 126 | 36669 (283/492 notes) | 6 {'query analysis': 5, 'research step': 1} | +0/-13 EM, answer_f1 CI -0.382 to -0.131, real |
