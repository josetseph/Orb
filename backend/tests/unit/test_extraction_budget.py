"""The extraction chunk budget is learned, because nothing reports it.

What binds is the model's output ceiling, not its context window — the prompt
emits ~2.5x its input as JSON. No API exposes that ceiling, but every call
reports whether it was truncated, so the budget is discovered from use.
"""

import json

import pytest

from app.services import extraction_budget as eb


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "resolve_data_dir", lambda: tmp_path)
    monkeypatch.delenv("ORB_EXTRACTION_CHUNK_TOKENS", raising=False)
    return tmp_path


class TestStartsAtTheDefault:
    def test_unknown_model_uses_the_seed(self):
        assert eb.learned_budget("gemini-x", 4000) == 4000

    def test_no_file_written_until_something_is_learned(self, store):
        eb.learned_budget("gemini-x", 4000)
        assert not (store / "extraction_budgets.json").exists()


class TestShrinkOnTruncation:
    def test_truncation_cuts_below_the_size_that_failed(self):
        eb.record_truncation("m", 4000)
        assert eb.learned_budget("m", 4000) == int(4000 * eb.SHRINK)

    def test_repeated_truncation_keeps_shrinking(self):
        eb.record_truncation("m", 4000)
        first = eb.learned_budget("m", 4000)
        eb.record_truncation("m", first)
        assert eb.learned_budget("m", 4000) < first

    def test_never_below_the_floor(self):
        for _ in range(30):
            eb.record_truncation("m", eb.learned_budget("m", 4000))
        assert eb.learned_budget("m", 4000) == eb.FLOOR

    def test_models_are_tracked_separately(self):
        eb.record_truncation("small", 4000)
        assert eb.learned_budget("small", 4000) < 4000
        assert eb.learned_budget("large", 4000) == 4000


class TestGrowOnSuccess:
    def test_needs_repeated_near_full_successes(self):
        for _ in range(eb.WINS_BEFORE_GROW - 1):
            eb.record_success("m", 3900, 4000)
        assert eb.learned_budget("m", 4000) == 4000
        eb.record_success("m", 3900, 4000)
        assert eb.learned_budget("m", 4000) == int(4000 * eb.GROW)

    def test_small_chunks_teach_nothing(self):
        """A 200-token note succeeding says nothing about a 4000-token ceiling."""
        for _ in range(20):
            eb.record_success("m", 200, 4000)
        assert eb.learned_budget("m", 4000) == 4000

    def test_growth_stops_at_the_hard_ceiling(self):
        for _ in range(400):
            current = eb.learned_budget("m", 4000)
            eb.record_success("m", current, current)
        assert eb.learned_budget("m", 4000) == eb.HARD_CEILING

    def test_a_truncation_resets_progress_toward_growth(self):
        eb.record_success("m", 3900, 4000)
        eb.record_success("m", 3900, 4000)
        eb.record_truncation("m", 4000)
        eb.record_success("m", 3900, 4000)
        # Two pre-truncation wins must not count toward the next promotion.
        assert eb.learned_budget("m", 4000) == int(4000 * eb.SHRINK)


class TestConvergence:
    def test_settles_near_a_model_true_ceiling(self):
        """Simulated model that truncates above 9000 input tokens."""
        real_limit = 9000
        for _ in range(60):
            budget = eb.learned_budget("m", 4000)
            if budget > real_limit:
                eb.record_truncation("m", budget)
            else:
                eb.record_success("m", budget, budget)
        final = eb.learned_budget("m", 4000)
        assert 4000 < final <= real_limit * eb.GROW
        assert final > 4000, "should have grown past the conservative seed"


class TestExplicitOverrideWins:
    def test_env_pins_the_value(self, monkeypatch):
        monkeypatch.setenv("ORB_EXTRACTION_CHUNK_TOKENS", "12000")
        assert eb.learned_budget("m", 4000) == 12000

    def test_env_also_disables_learning(self, monkeypatch, store):
        monkeypatch.setenv("ORB_EXTRACTION_CHUNK_TOKENS", "12000")
        eb.record_truncation("m", 12000)
        assert eb.learned_budget("m", 4000) == 12000
        assert not (store / "extraction_budgets.json").exists()

    def test_a_bad_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv("ORB_EXTRACTION_CHUNK_TOKENS", "not-a-number")
        assert eb.learned_budget("m", 4000) == 4000


class TestPersistence:
    def test_survives_a_reload(self, store):
        eb.record_truncation("m", 4000)
        expected = eb.learned_budget("m", 4000)
        data = json.loads((store / "extraction_budgets.json").read_text())
        assert data["m"]["budget"] == expected

    def test_corrupt_store_falls_back_to_the_default(self, store):
        (store / "extraction_budgets.json").write_text("{ not json")
        assert eb.learned_budget("m", 4000) == 4000

    def test_none_model_is_handled(self):
        eb.record_truncation(None, 4000)
        assert eb.learned_budget(None, 4000) < 4000
