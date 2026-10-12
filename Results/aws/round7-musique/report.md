# Sweep round7-musique

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5, "INDEX_RAW_PASSAGES": true}`
Varied: `{"INDEX_RAW_PASSAGES": [false], "PPR_EXPANSION": [true], "CONTEXT_ORDER": ["edges"], "FIRST_SEARCH_IS_QUESTION": [true]}` (one-at-a-time)

## Rung: first 50 dev questions (6 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.293 | 26% | 0.567 | 0.578 | 183 | 17028 (410/526 notes) | 15 {'research step': 7, 'query analysis': 8} |  |
| INDEX_RAW_PASSAGES=False | 0.215 | 14% | 0.635 | 0.677 | 394 | 15073 (438/526 notes) | 12 {'research step': 6, 'query analysis': 6} | +2/-8 EM, answer_f1 CI -0.201 to +0.044, within noise |
| PPR_EXPANSION=True | 0.289 | 20% | 0.568 | 0.578 | 582 | 17028 (410/526 notes) | 12 {'query analysis': 8, 'research step': 4} | +1/-4 EM, answer_f1 CI -0.097 to +0.090, within noise |
| CONTEXT_ORDER=edges | 0.346 | 26% | 0.552 | 0.578 | 459 | 17028 (410/526 notes) | 13 {'query analysis': 9, 'research step': 4} | +2/-2 EM, answer_f1 CI -0.011 to +0.129, within noise |
| FIRST_SEARCH_IS_QUESTION=True | 0.340 | 28% | 0.572 | 0.578 | 570 | 17028 (410/526 notes) | 17 {'query analysis': 9, 'research step': 8} | +3/-2 EM, answer_f1 CI -0.028 to +0.133, within noise |
| PASSAGE_KEYWORD_SEARCH=True,PASSAGE_TITLE_HEADER=True | 0.363 | 28% | 0.500 | 0.543 | 1054 | 17178 (410/526 notes) | 11 {'research step': 4, 'query analysis': 7} | +3/-2 EM, answer_f1 CI -0.008 to +0.157, within noise |
