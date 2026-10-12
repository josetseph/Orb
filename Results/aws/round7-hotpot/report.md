# Sweep round7-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "INDEX_RAW_PASSAGES": true}`
Varied: `{"INDEX_RAW_PASSAGES": [false], "PASSAGE_KEYWORD_SEARCH": [true], "PPR_EXPANSION": [true], "CONTEXT_ORDER": ["edges"], "FIRST_SEARCH_IS_QUESTION": [true]}` (one-at-a-time)

## Rung: first 50 dev questions (6 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.573 | 42% | 0.960 | 1.000 | 258 | 5774 (484/492 notes) | 7 {'query analysis': 6, 'research step': 1} |  |
| INDEX_RAW_PASSAGES=False | 0.524 | 42% | 0.950 | 1.000 | 274 | 5749 (486/492 notes) | 14 {'query analysis': 9, 'research step': 5} | +6/-6 EM, answer_f1 CI -0.180 to +0.083, within noise |
| PASSAGE_KEYWORD_SEARCH=True | 0.658 | 54% | 0.940 | 1.000 | 381 | 5856 (485/492 notes) | 9 {'query analysis': 7, 'research step': 2} | +8/-2 EM, answer_f1 CI -0.029 to +0.201, within noise |
| PPR_EXPANSION=True | 0.635 | 46% | 0.960 | 1.000 | 238 | 5774 (484/492 notes) | 11 {'query analysis': 8, 'research step': 3} | +5/-3 EM, answer_f1 CI -0.021 to +0.155, within noise |
| CONTEXT_ORDER=edges | 0.611 | 50% | 0.940 | 1.000 | 233 | 5774 (484/492 notes) | 10 {'query analysis': 8, 'research step': 2} | +7/-3 EM, answer_f1 CI -0.083 to +0.159, within noise |
| FIRST_SEARCH_IS_QUESTION=True | 0.679 | 56% | 0.990 | 1.000 | 135 | 5774 (484/492 notes) | 8 {'query analysis': 4, 'research step': 4} | +10/-3 EM, answer_f1 CI -0.009 to +0.219, within noise |
