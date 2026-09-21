"""The sweep planner: which variants exist, which index each needs, who survives a rung."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import sweep  # noqa: E402
from levers import BY_NAME, LEVERS, STAGES  # noqa: E402

SPEC = {
    "name": "t", "dataset": "hotpotqa", "dev": [0, 20],
    "baseline": {"provider": "local", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-e4b-q4"},
    "vary": {"MAX_LOOP_ITERATIONS": [3, 5], "RERANKER_TOP_K": [5, 20]},
}


def test_every_lever_names_a_known_stage_and_is_declared_once():
    assert all(lever.stage in STAGES and lever.how in ("set", "flag") for lever in LEVERS)
    assert len(BY_NAME) == len(LEVERS)


def test_one_at_a_time_skips_values_equal_to_the_default():
    names = [v["name"] for v in sweep.variants(SPEC)]
    assert names == ["base", "MAX_LOOP_ITERATIONS=5", "RERANKER_TOP_K=5", "RERANKER_TOP_K=20"]


def test_grid_is_the_full_product_minus_the_baseline():
    names = [v["name"] for v in sweep.variants({**SPEC, "design": "grid"})]
    assert len(names) == 1 + 2 * 2 and names[0] == "base"


def test_an_unknown_lever_stops_the_sweep():
    import pytest

    with pytest.raises(SystemExit, match="unknown lever"):
        sweep.variants({**SPEC, "vary": {"NOT_A_LEVER": [1]}})


def test_only_extract_and_index_levers_change_the_index():
    base = SPEC["baseline"]
    same = sweep.index_name({**base, "MAX_LOOP_ITERATIONS": 8, "RERANKER_TOP_K": 5, "chat_model": "qwen35-4b-q4"}, SPEC)
    assert same == sweep.index_name(base, SPEC)
    for lever, value in (("ingestion_model", "qwen35-4b-q4"), ("EMBED_MODEL_ID", "qwen3-embed-0.6b-q8"), ("communities", True)):
        assert sweep.index_name({**base, lever: value}, SPEC) != same
    assert sweep.index_name(base, {**SPEC, "holdout": [20, 30]}) != same, "a wider question span needs more notes"


def test_lever_args_speak_experiment_py():
    levers = {"provider": "local", "chat_model": "m", "MAX_LOOP_ITERATIONS": 5, "communities": True}
    assert sweep.lever_args(levers) == ["--provider", "local", "--chat-model", "m", "--set", "MAX_LOOP_ITERATIONS=5", "--communities"]
    assert sweep.lever_args(levers, stages=("extract", "index")) == ["--provider", "local", "--communities"]


def test_the_baseline_always_survives_and_the_best_half_joins_it():
    scored = [("base", 0.1), ("a", 0.9), ("b", 0.5), ("c", 0.7), ("d", None)]
    assert sweep.survivors(scored, 0.5) == ["base", "a", "c"]


def test_a_named_snapshot_stands_in_for_the_baseline_index_only():
    spec = {**SPEC, "index": "hp20-e4b"}
    assert sweep.index_name(SPEC["baseline"], spec) == "hp20-e4b"
    assert sweep.index_name({**SPEC["baseline"], "RERANKER_TOP_K": 5}, spec) == "hp20-e4b"
    assert sweep.index_name({**SPEC["baseline"], "ingestion_model": "qwen35-4b-q4"}, spec) != "hp20-e4b"
