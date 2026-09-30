# Sweep round0-speed

Dataset hotpotqa, dev questions 0 to 1, evaluator full, metric answer_f1.
Baseline: `{"provider": "local", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-e4b-q4"}`
Varied: `{"JSON_CONSTRAINED_DECODING": [true, false], "LLAMA_FLASH_ATTN": [false, true]}` (grid)

## Rung: first 1 dev questions (4 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 1.000 | 100% | 1.000 | 100 | 1717 | 0  |  |
| LLAMA_FLASH_ATTN=True | 1.000 | 100% | 1.000 | 168 | 1654 | 0  | +0/-0 EM, answer_f1 CI +0.000 to +0.000, within noise |
| JSON_CONSTRAINED_DECODING=False | 0.000 | 0% | 0.000 | 12 | 87 | 1 {'research step': 1} | +0/-1 EM, answer_f1 CI -1.000 to -1.000, within noise |
| JSON_CONSTRAINED_DECODING=False,LLAMA_FLASH_ATTN=True | 0.000 | 0% | 0.000 | 12 | 87 | 1 {'research step': 1} | +0/-1 EM, answer_f1 CI -1.000 to -1.000, within noise |
