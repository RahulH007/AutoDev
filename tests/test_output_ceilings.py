"""Per-purpose ceilings on a single completion.

One global ``LLM_MAX_OUTPUT_TOKENS`` has to be set for the largest thing the
pipeline asks for -- a developer service -- and every other call then reserves
that same figure against the per-minute window whether or not it could ever use
it. A QA triage answer is a list of file paths; reserving 4,096 tokens for it
holds half of an 8,000 token window for nothing.

So the ceiling is chosen per :class:`Purpose`, with the global value as the
fallback for a purpose that names no ceiling of its own. The resolved figure is
what the provider client is built with *and* what the budget reserves, because a
disagreement between those two is exactly how a request the budget waved through
came back as a 413.
"""

from __future__ import annotations

import pytest

from core.config import LLMProvider, Purpose, Settings, get_settings, reset_settings_cache
from llm import registry
from llm.budget import BudgetExceededError, budget_for, reset_budgets
from tests.test_config_and_registry import MeteredFakeModel


class TestTheDefaults:
    """Sized to what each purpose actually returns, measured on real runs."""

    def test_heavy_keeps_the_largest_ceiling(self):
        """A developer service is the biggest single answer the pipeline asks for."""
        assert Settings().max_output_for(Purpose.HEAVY) == 4096

    def test_structured_is_smaller_than_heavy(self):
        """A PRD or an architecture document: large, but not a codebase."""
        assert Settings().max_output_for(Purpose.STRUCTURED) == 3500

    def test_text_is_smaller_again(self):
        assert Settings().max_output_for(Purpose.TEXT) == 2000

    def test_cheap_is_the_smallest(self):
        """QA triage answers with a list of file paths."""
        assert Settings().max_output_for(Purpose.CHEAP) == 1000

    def test_the_global_ceiling_is_unchanged(self):
        assert Settings().llm_max_output_tokens == 4096


class TestResolution:
    def test_a_purpose_without_its_own_ceiling_falls_back_to_the_global_one(self):
        settings = Settings(llm_max_output_tokens=777, max_output_heavy=None)
        assert settings.max_output_for(Purpose.HEAVY) == 777

    def test_each_purpose_is_settable_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MAX_OUTPUT_HEAVY", "6000")
        monkeypatch.setenv("MAX_OUTPUT_STRUCTURED", "3000")
        monkeypatch.setenv("MAX_OUTPUT_TEXT", "1500")
        monkeypatch.setenv("MAX_OUTPUT_CHEAP", "500")
        reset_settings_cache()

        settings = get_settings()
        assert settings.max_output_for(Purpose.HEAVY) == 6000
        assert settings.max_output_for(Purpose.STRUCTURED) == 3000
        assert settings.max_output_for(Purpose.TEXT) == 1500
        assert settings.max_output_for(Purpose.CHEAP) == 500

    def test_overriding_one_purpose_leaves_the_others_alone(self):
        """The same isolation MODEL_HEAVY has; this follows that pattern."""
        settings = Settings(max_output_heavy=6000)
        assert settings.max_output_for(Purpose.HEAVY) == 6000
        assert settings.max_output_for(Purpose.CHEAP) == 1000

    def test_zero_still_means_the_provider_decides(self):
        """The global ceiling already spells opt-out this way."""
        settings = Settings(max_output_cheap=0)
        assert settings.max_output_for(Purpose.CHEAP) == 0


class TestTheClientIsBuiltWithIt:
    def test_a_cheap_client_asks_for_the_cheap_ceiling(self):
        registry.reset_cache()
        settings = Settings(google_api_key="k")
        model = registry.get_chat_model(Purpose.CHEAP, LLMProvider.GOOGLE, settings)
        assert model.max_output_tokens == 1000

    def test_a_heavy_client_asks_for_the_heavy_ceiling(self):
        registry.reset_cache()
        settings = Settings(google_api_key="k")
        model = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings)
        assert model.max_output_tokens == 4096

    def test_a_purpose_opted_out_hands_the_decision_back_to_the_provider(self):
        registry.reset_cache()
        settings = Settings(groq_api_key="k", max_output_cheap=0)
        model = registry.get_chat_model(Purpose.CHEAP, LLMProvider.GROQ, settings)
        assert model.max_tokens is None


class TestTheCacheKeyKeepsThemApart:
    """Two purposes can name the same model and still need different clients."""

    def test_two_ceilings_on_one_model_are_two_clients(self):
        registry.reset_cache()
        settings = Settings(
            google_api_key="k",
            model_heavy="one-model",
            model_cheap="one-model",
        )

        heavy = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings)
        cheap = registry.get_chat_model(Purpose.CHEAP, LLMProvider.GOOGLE, settings)

        assert heavy is not cheap
        assert heavy.max_output_tokens == 4096
        assert cheap.max_output_tokens == 1000

    def test_one_ceiling_on_one_model_is_still_one_client(self):
        """Sharing is the point of the cache; only the resolved figure splits it."""
        registry.reset_cache()
        settings = Settings(
            google_api_key="k",
            model_heavy="one-model",
            model_cheap="one-model",
            max_output_cheap=4096,
        )

        heavy = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings)
        cheap = registry.get_chat_model(Purpose.CHEAP, LLMProvider.GOOGLE, settings)

        assert heavy is cheap


class TestTheBudgetReservesIt:
    """The reservation and the client must name the same number.

    A budget that reserves more than the client will ever ask for wastes the
    window; one that reserves less lets through a request the provider refuses.
    """

    @staticmethod
    def _budget(purpose: Purpose):
        settings = get_settings()
        model = registry.model_name_for(LLMProvider.GOOGLE, purpose, settings)
        return budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)

    def test_a_cheap_call_reserves_the_cheap_ceiling(self, monkeypatch: pytest.MonkeyPatch):
        reset_budgets()
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        monkeypatch.setenv("MODEL_CHEAP", "cheap-model")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.CHEAP)

        used = self._budget(Purpose.CHEAP).used()
        assert 1000 <= used < 1100

    def test_a_text_call_reserves_the_text_ceiling(self, monkeypatch: pytest.MonkeyPatch):
        reset_budgets()
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        monkeypatch.setenv("MODEL_TEXT", "text-model")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.TEXT)

        used = self._budget(Purpose.TEXT).used()
        assert 2000 <= used < 2100

    def test_the_reserve_still_wins_when_it_is_larger(self, monkeypatch: pytest.MonkeyPatch):
        """A ceiling of 1,000 does not license reserving less than the floor."""
        reset_budgets()
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "2000")
        monkeypatch.setenv("MODEL_CHEAP", "cheap-model")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.CHEAP)

        assert self._budget(Purpose.CHEAP).used() >= 2000

    def test_a_small_window_fits_a_cheap_call_that_a_heavy_ceiling_would_refuse(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The whole point: 1,200 tokens of window has room for triage."""
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "1200")
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.CHEAP)
        assert model.calls == 1

        with pytest.raises(BudgetExceededError):
            registry.llm_call("hello there", Purpose.HEAVY)
