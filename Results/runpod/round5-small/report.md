# Sweep round5-small

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-e4b-q4"}`
Varied: `{"EXTRACTION_ATTEMPTS": [2]}` (one-at-a-time)

## Rung: first 50 dev questions (4 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.423 | 32% | 0.690 | 0.730 | 216 | 3575 (292/492 notes) | 11 {'query analysis': 10, 'research step': 1} |  |
| EXTRACTION_ATTEMPTS=2 | 0.447 | 34% | 0.670 | 0.720 | 566 | 5910 (342/492 notes) | 11 {'query analysis': 9, 'research step': 2} | +7/-6 EM, answer_f1 CI -0.111 to +0.155, within noise |
| ingestion_model=qwen35-4b-q4,EXTRACTION_ENABLE_THINKING=False,EXTRACTION_PROMPT_SUFFIX=#e1451c73 | 0.444 | 34% | 0.740 | 0.790 | 315 | 2778 (396/492 notes) | 9 {'query analysis': 6, 'research step': 3} | +12/-11 EM, answer_f1 CI -0.157 to +0.197, within noise |
| ingestion_model=qwen35-0.8b-q4,EXTRACTION_ENABLE_THINKING=False,EXTRACTION_PROMPT_SUFFIX=#e1451c73 | 0.214 | 12% | 0.500 | 0.510 | 124 | 9026 (235/492 notes) | 9 {'query analysis': 6, 'research step': 3} | +3/-13 EM, answer_f1 CI -0.350 to -0.066, real |
