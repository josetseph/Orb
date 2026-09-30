# Sweep round1-extraction

Dataset hotpotqa, dev questions 0 to 20, evaluator full, metric answer_f1.
Baseline: `{"chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-e4b-q4", "EXTRACTION_JSON_CONSTRAINED": false}`
Varied: `{"ingestion_model": ["gemma4-12b-q4", "gemma4-e2b-q4"], "EXTRACTION_MODE": ["task_split"], "EXTRACTION_JSON_CONSTRAINED": [true]}` (one-at-a-time)

## Rung: first 20 dev questions (5 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.429 | 30% | 0.550 | 95 | 8139 (120/199 notes) | 0  |  |
| ingestion_model=gemma4-12b-q4 | 0.767 | 55% | 0.875 | 108 | 15131 (195/199 notes) | 1 {'research step': 1} | +6/-1 EM, answer_f1 CI +0.115 to +0.544, within noise |
| ingestion_model=gemma4-e2b-q4 | 0.322 | 15% | 0.500 | 78 | 6019 (100/199 notes) | 1 {'research step': 1} | +0/-3 EM, answer_f1 CI -0.321 to +0.094, within noise |
| EXTRACTION_MODE=task_split | 0.340 | 30% | 0.525 | 87 | 6667 (96/199 notes) | 0  | +3/-3 EM, answer_f1 CI -0.322 to +0.161, within noise |
| EXTRACTION_JSON_CONSTRAINED=True | 0.211 | 15% | 0.425 | 112 | 30543 (72/199 notes) | 1 {'research step': 1} | +0/-3 EM, answer_f1 CI -0.385 to -0.065, within noise |
