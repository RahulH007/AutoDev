"""Choosing how much model a task is worth, from what the architecture says.

A developer service used to get the same model whether it was a three-file worker
or a thirty-endpoint API. The architecture already distinguishes them — it names
the files, the endpoints, the data models and the dependencies — so this turns
that into a deterministic tier, and the registry into something that spends
proportionately.

Three things are checked throughout, and the last two matter as much as the
first. That the policy is deterministic and explainable. That it never *raises* a
tier for a cheap task, only lowers one it cannot afford. And that with the flag
off, nothing whatsoever is different.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from core.config import (
    LLMProvider,
    Purpose,
    Settings,
    get_settings,
    reset_settings_cache,
)
from llm import registry
from llm.accounting import recording
from llm.budget import budget_for, reset_budgets
from llm.routing import (
    ModelTier,
    RouteSignal,
    difficulty_for,
    plan_for,
)
from state.state import Stage

SECRET = "sk-groq-never-log-me"

# The two Groq models the default tier table names.
SMALL, LARGE = "openai/gpt-oss-20b", "openai/gpt-oss-120b"


def routed(**overrides: Any) -> Settings:
    """Routing on, one Groq key, everything else default."""
    return Settings(
        llm_provider=LLMProvider.GROQ,
        groq_api_key=SECRET,
        routing_enabled=True,
        **overrides,
    )


def pooled(count: int = 4, **overrides: Any) -> Settings:
    keys = {f"groq_api_key_{i}": f"{SECRET}-{i}" for i in range(1, count + 1)}
    return Settings(
        llm_provider=LLMProvider.GROQ, routing_enabled=True, **keys, **overrides
    )


# Three services drawn to sit in each band; see the report for the arithmetic.
TINY = RouteSignal(key_files=2, endpoints=1, data_models=1)          # 2*2+1+1     = 6
TYPICAL = RouteSignal(key_files=8, endpoints=12, data_models=5)      # 16+12+5     = 33
LARGE_SERVICE = RouteSignal(                                          # 28+25+9+4   = 66
    key_files=14, endpoints=25, data_models=9, dependencies=2
)


class ModelFake(Runnable):
    """A client that remembers which model name it was built for.

    Answers on both paths — plain text and the structured ladder's native rung —
    so a test can drive either and still ask which model was reached.
    """

    def __init__(self, model: str = "?") -> None:
        self.model = model
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        return AIMessage(content=self.model)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        return self.invoke(input, config)

    def with_structured_output(
        self, schema: type, method: str | None = None, **kwargs: Any
    ) -> Runnable:
        from langchain_core.runnables import RunnableLambda

        if (method or "default") != "default":
            raise NotImplementedError(f"{method} is not supported by this model")

        def run(_prompt: Any) -> Any:
            self.calls += 1
            return schema(name="sprocket", size=3)

        return RunnableLambda(run)


def _install_by_model(monkeypatch: pytest.MonkeyPatch) -> dict[str, ModelFake]:
    """Serve a distinct fake per resolved model name, as the real builder would."""
    built: dict[str, ModelFake] = {}

    def build(purpose, provider, settings=None, ceiling=None, account=None, tier=None):
        name = registry.model_name_for(provider, purpose, settings, tier)
        return built.setdefault(name, ModelFake(name))

    monkeypatch.setattr(registry, "get_chat_model", build)
    return built


def _budget(settings: Settings, model: str, account: str | None = None):
    return budget_for(LLMProvider.GROQ, model, settings.llm_tokens_per_minute, account)


# ── The score ────────────────────────────────────────────────────


class TestScoring:
    def test_the_score_is_the_weighted_sum(self):
        assert RouteSignal(key_files=3, endpoints=4, data_models=2, dependencies=1).score == (
            2 * 3 + 4 + 2 + 2 * 1
        )

    def test_it_is_deterministic(self):
        assert TYPICAL.score == TYPICAL.score == 33

    def test_an_empty_signal_scores_nothing(self):
        assert RouteSignal().score == 0
        assert RouteSignal().is_empty

    def test_negative_counts_cannot_reduce_a_score(self):
        """Defensive: a count is a length, but the arithmetic should not depend on it."""
        assert RouteSignal(key_files=4, endpoints=-100).score == 8

    def test_the_explanation_shows_the_arithmetic(self):
        explanation = RouteSignal(key_files=2, endpoints=1, data_models=1).explain()

        assert "2 file(s)×2" in explanation
        assert explanation.endswith("= 6")

    def test_files_weigh_more_than_endpoints(self):
        """A file is the closest thing the architecture has to a measure of code."""
        assert RouteSignal(key_files=1).score > RouteSignal(endpoints=1).score

    def test_a_dependency_weighs_as_much_as_a_file(self):
        assert RouteSignal(dependencies=1).score == RouteSignal(key_files=1).score


class TestClassification:
    def test_a_simple_service_is_low(self):
        assert difficulty_for(TINY, routed()) is ModelTier.LOW

    def test_a_moderate_service_is_medium(self):
        assert difficulty_for(TYPICAL, routed()) is ModelTier.MEDIUM

    def test_a_complex_service_is_high(self):
        assert difficulty_for(LARGE_SERVICE, routed()) is ModelTier.HIGH

    def test_the_boundaries_are_exact(self):
        settings = routed()

        assert difficulty_for(RouteSignal(endpoints=11), settings) is ModelTier.LOW
        assert difficulty_for(RouteSignal(endpoints=12), settings) is ModelTier.MEDIUM
        assert difficulty_for(RouteSignal(endpoints=47), settings) is ModelTier.MEDIUM
        assert difficulty_for(RouteSignal(endpoints=48), settings) is ModelTier.HIGH

    def test_the_thresholds_are_configurable(self):
        settings = routed(routing_low_max_score=100)

        assert difficulty_for(TYPICAL, settings) is ModelTier.LOW

    def test_an_ordinary_service_is_not_routinely_high(self):
        """Otherwise the policy costs more than the static one it replaced.

        These are the shapes AgentForge actually generates from a paragraph of
        requirements — up to ten files and fifteen endpoints. Only work beyond
        that should reach the top tier.
        """
        for files, endpoints, models in ((6, 8, 4), (8, 12, 5), (10, 15, 6)):
            signal = RouteSignal(key_files=files, endpoints=endpoints, data_models=models)
            assert difficulty_for(signal, routed()) is not ModelTier.HIGH

    def test_classification_is_repeatable(self):
        assert difficulty_for(TYPICAL, routed()) is difficulty_for(TYPICAL, routed())


# ── The plan ─────────────────────────────────────────────────────


class TestPlanning:
    def test_a_plan_carries_its_score_and_reasoning(self):
        plan = plan_for(Purpose.HEAVY, TYPICAL, routed())

        assert plan.tier is ModelTier.MEDIUM
        assert plan.score == 33
        assert "8 file(s)" in plan.reason

    def test_the_description_is_a_single_readable_line(self):
        assert "medium tier, score 33" in plan_for(Purpose.HEAVY, TYPICAL, routed()).describe()

    def test_a_disabled_flag_routes_nothing(self):
        plan = plan_for(Purpose.HEAVY, TYPICAL, Settings(groq_api_key=SECRET))

        assert plan.routed is False
        assert plan.tier is None
        assert "disabled" in plan.reason

    def test_an_unroutable_purpose_is_left_alone(self):
        """Only the developer path has structure rich enough to score."""
        for purpose in (Purpose.STRUCTURED, Purpose.TEXT, Purpose.CHEAP):
            plan = plan_for(purpose, TYPICAL, routed())
            assert plan.routed is False
            assert purpose.value in plan.reason

    def test_no_signal_routes_nothing(self):
        assert plan_for(Purpose.HEAVY, None, routed()).routed is False

    def test_an_empty_signal_routes_nothing(self):
        """An architecture that described nothing is not evidence of an easy task."""
        plan = plan_for(Purpose.HEAVY, RouteSignal(), routed())

        assert plan.routed is False
        assert "nothing measurable" in plan.reason


class TestEligibility:
    def test_an_eligible_difficulty_tier_is_chosen(self):
        plan = plan_for(Purpose.HEAVY, LARGE_SERVICE, routed(), frozenset(ModelTier))

        assert plan.tier is ModelTier.HIGH
        assert plan.stepped_down is False

    def test_an_ineligible_tier_steps_down(self):
        plan = plan_for(
            Purpose.HEAVY,
            LARGE_SERVICE,
            routed(),
            frozenset({ModelTier.LOW, ModelTier.MEDIUM}),
        )

        assert plan.tier is ModelTier.MEDIUM
        assert plan.difficulty is ModelTier.HIGH
        assert plan.stepped_down is True

    def test_it_steps_down_to_the_strongest_thing_available(self):
        plan = plan_for(
            Purpose.HEAVY, LARGE_SERVICE, routed(), frozenset({ModelTier.LOW})
        )

        assert plan.tier is ModelTier.LOW

    def test_a_cheap_task_is_never_promoted(self):
        """The objective is the cheapest appropriate model, not the freest one."""
        plan = plan_for(Purpose.HEAVY, TINY, routed(), frozenset(ModelTier))

        assert plan.tier is ModelTier.LOW

    def test_nothing_eligible_keeps_the_difficulty_tier(self):
        """So the budget refuses the call exactly as it always has."""
        plan = plan_for(Purpose.HEAVY, LARGE_SERVICE, routed(), frozenset())

        assert plan.tier is ModelTier.HIGH
        assert "no weaker tier" in plan.reason


class TestTheModuleStaysPure:
    def test_it_does_not_read_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        """Every input arrives as an argument; only core.config reads os.environ."""
        import llm.routing as routing_module

        source = __import__("inspect").getsource(routing_module)
        assert "os.environ" not in source
        assert "getenv" not in source

    def test_it_builds_no_clients_and_makes_no_calls(self):
        import llm.routing as routing_module

        source = __import__("inspect").getsource(routing_module)
        for forbidden in ("get_chat_model", "invoke", "budget_for", "requests", "httpx"):
            assert forbidden not in source

    def test_a_plan_is_immutable(self):
        import dataclasses

        plan = plan_for(Purpose.HEAVY, TYPICAL, routed())

        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.tier = ModelTier.HIGH  # type: ignore[misc]


# ── Model resolution ─────────────────────────────────────────────


class TestModelResolution:
    def test_each_tier_resolves_to_its_configured_model(self):
        settings = routed(
            groq_model_low="tiny", groq_model_medium="mid", groq_model_high="big"
        )

        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, settings, ModelTier.LOW
        ) == "tiny"
        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, settings, ModelTier.HIGH
        ) == "big"

    def test_the_defaults_map_low_to_the_smaller_model(self):
        settings = routed()

        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, settings, ModelTier.LOW
        ) == SMALL
        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, settings, ModelTier.HIGH
        ) == LARGE

    def test_an_explicit_override_still_outranks_a_tier(self):
        """A named MODEL_HEAVY is a decision already taken."""
        settings = routed(model_heavy="chosen-by-hand", groq_model_low="tiny")

        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, settings, ModelTier.LOW
        ) == "chosen-by-hand"

    def test_no_tier_resolves_exactly_as_before(self):
        settings = routed()

        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, settings
        ) == registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, Settings(groq_api_key=SECRET)
        )

    def test_an_untiered_provider_has_no_tier_models(self):
        settings = Settings(google_api_key="k", routing_enabled=True)

        for tier in ModelTier:
            assert registry.tier_model_for(LLMProvider.GOOGLE, tier, settings) is None

    def test_an_untiered_provider_falls_back_to_its_purpose_default(self):
        settings = Settings(google_api_key="k", routing_enabled=True)

        assert registry.model_name_for(
            LLMProvider.GOOGLE, Purpose.HEAVY, settings, ModelTier.LOW
        ) == registry.model_name_for(LLMProvider.GOOGLE, Purpose.HEAVY, settings)


# ── Through the registry ─────────────────────────────────────────


class TestRoutingThroughTheRegistry:
    def test_a_simple_service_is_written_by_the_small_model(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        built = _install_by_model(monkeypatch)
        reset_budgets()

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, routed(), signal=TINY
        ).invoke("go")

        assert built[SMALL].calls == 1
        assert LARGE not in built or built[LARGE].calls == 0

    def test_a_complex_service_is_written_by_the_large_model(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        built = _install_by_model(monkeypatch)
        reset_budgets()

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, routed(), signal=LARGE_SERVICE
        ).invoke("go")

        assert built[LARGE].calls == 1

    def test_two_services_in_one_run_can_take_different_tiers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        built = _install_by_model(monkeypatch)
        reset_budgets()
        settings = routed()

        registry.get_structured_llm(_Widget(), Purpose.HEAVY, settings, signal=TINY).invoke("go")
        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, settings, signal=LARGE_SERVICE
        ).invoke("go")

        assert built[SMALL].calls == 1
        assert built[LARGE].calls == 1

    def test_an_unroutable_purpose_keeps_its_model(self, monkeypatch: pytest.MonkeyPatch):
        built = _install_by_model(monkeypatch)
        reset_budgets()

        registry.get_text_llm(Purpose.TEXT, routed()).invoke("hello there")

        assert list(built) == [
            registry.model_name_for(LLMProvider.GROQ, Purpose.TEXT, routed())
        ]

    def test_routing_does_not_add_a_model_call(self, monkeypatch: pytest.MonkeyPatch):
        """The decision is arithmetic, not a question anyone is asked."""
        built = _install_by_model(monkeypatch)
        reset_budgets()

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, routed(), signal=TYPICAL
        ).invoke("go")

        assert sum(fake.calls for fake in built.values()) == 1


class TestBudgetAwareness:
    def test_a_tier_whose_window_is_full_steps_down(self, monkeypatch: pytest.MonkeyPatch):
        built = _install_by_model(monkeypatch)
        reset_budgets()
        settings = routed(llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, LARGE).record_now(10_000)

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, settings, signal=LARGE_SERVICE
        ).invoke("go")

        assert built[SMALL].calls == 1

    def test_a_tier_too_large_for_the_window_is_not_chosen(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A reservation bigger than the whole window can never succeed."""
        _install_by_model(monkeypatch)
        reset_budgets()
        settings = routed(llm_tokens_per_minute=100, llm_output_reserve=0)

        plan = registry._route(
            Purpose.HEAVY, LARGE_SERVICE, settings, reserve=4096
        )

        assert plan.tier is ModelTier.HIGH
        assert "no weaker tier" in plan.reason

    def test_headroom_on_one_account_keeps_a_tier_eligible(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A pooled tier needs only one account with room to stay available."""
        _install_by_model(monkeypatch)
        reset_budgets()
        settings = pooled(2, llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, LARGE, "groq-1").record_now(10_000)

        plan = registry._route(Purpose.HEAVY, LARGE_SERVICE, settings, reserve=1_000)

        assert plan.tier is ModelTier.HIGH

    def test_the_pool_still_picks_the_account_within_the_chosen_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        clients: dict[tuple[str, str | None], ModelFake] = {}

        def build(purpose, provider, settings=None, ceiling=None, account=None, tier=None):
            name = registry.model_name_for(provider, purpose, settings, tier)
            key = (name, account.id if account else None)
            return clients.setdefault(key, ModelFake(name))

        monkeypatch.setattr(registry, "get_chat_model", build)
        reset_budgets()
        settings = pooled(2, llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, SMALL, "groq-1").record_now(10_000)

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, settings, signal=TINY
        ).invoke("go")

        assert clients[(SMALL, "groq-1")].calls == 0
        assert clients[(SMALL, "groq-2")].calls == 1


class TestAdaptiveCeilingsAgree:
    def test_the_ceiling_is_the_same_whatever_the_tier(self):
        """Ceiling and tier are independent: how big the answer may be does not
        depend on which model writes it."""
        settings = routed(adaptive_ceilings=True)

        assert settings.max_output_for(Purpose.HEAVY, 1800) == 2048

    def test_the_reservation_matches_the_resolved_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _install_by_model(monkeypatch)
        reset_budgets()
        settings = routed(adaptive_ceilings=True, llm_output_reserve=0)

        with recording("run-1", Stage.DEVELOPER.value) as ledger:
            registry.get_structured_llm(
                _Widget(), Purpose.HEAVY, settings, demand=1800, signal=TINY
            ).invoke("go")

        entry = ledger.records[0]
        assert entry.ceiling_tokens == 2048
        assert entry.reserved_tokens == entry.estimated_tokens + 2048
        assert _budget(settings, SMALL).used() == entry.reserved_tokens

    def test_the_routed_model_is_the_one_that_was_charged(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _install_by_model(monkeypatch)
        reset_budgets()
        settings = routed(adaptive_ceilings=True, llm_output_reserve=0)

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, settings, demand=1800, signal=LARGE_SERVICE
        ).invoke("go")

        assert _budget(settings, LARGE).used() > 0
        assert _budget(settings, SMALL).used() == 0


class TestTheCacheKeepsModelsApart:
    def test_two_tiers_never_share_a_client(self):
        registry.reset_cache()
        settings = routed()

        low = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GROQ, settings, None, None, ModelTier.LOW
        )
        high = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GROQ, settings, None, None, ModelTier.HIGH
        )

        assert low is not high
        assert low.model_name == SMALL
        assert high.model_name == LARGE

    def test_two_tiers_naming_one_model_do_share(self):
        """Correctly: same model, same account, same ceiling is the same client."""
        registry.reset_cache()
        settings = routed()

        medium = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GROQ, settings, None, None, ModelTier.MEDIUM
        )
        high = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GROQ, settings, None, None, ModelTier.HIGH
        )

        assert medium is high

    def test_a_tier_and_an_account_are_both_in_the_identity(self):
        registry.reset_cache()
        settings = pooled(2)
        first, second = settings.accounts_for(LLMProvider.GROQ)

        clients = [
            registry.get_chat_model(
                Purpose.HEAVY, LLMProvider.GROQ, settings, None, account, tier
            )
            for account in (first, second)
            for tier in (ModelTier.LOW, ModelTier.HIGH)
        ]

        assert len({id(client) for client in clients}) == 4


# ── Observability ────────────────────────────────────────────────


class TestTheLedgerRecordsTheRoute:
    def _record(self, monkeypatch: pytest.MonkeyPatch, settings: Settings, signal):
        _install_by_model(monkeypatch)
        reset_budgets()
        with recording("run-1", Stage.DEVELOPER.value) as ledger:
            registry.get_structured_llm(
                _Widget(), Purpose.HEAVY, settings, signal=signal
            ).invoke("go")
        return ledger

    def test_the_tier_is_recorded(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, routed(), TINY)

        assert ledger.records[0].tier == "low"
        assert ledger.records[0].difficulty == "low"

    def test_a_stepped_down_route_records_both_tiers(self, monkeypatch: pytest.MonkeyPatch):
        """Filled after the reset, so the exhausted window is what routing sees."""
        _install_by_model(monkeypatch)
        reset_budgets()
        settings = routed(llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, LARGE).record_now(10_000)

        with recording("run-1", Stage.DEVELOPER.value) as ledger:
            registry.get_structured_llm(
                _Widget(), Purpose.HEAVY, settings, signal=LARGE_SERVICE
            ).invoke("go")

        assert ledger.records[0].difficulty == "high"
        assert ledger.records[0].tier == "low"

    def test_the_report_breaks_down_by_tier(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, routed(), TYPICAL)

        assert set(ledger.report()["by_tier"]) == {"medium"}

    def test_an_unrouted_report_has_no_tier_section(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, Settings(groq_api_key=SECRET), TYPICAL)

        assert "by_tier" not in ledger.report()
        assert ledger.records[0].tier is None

    def test_merging_keeps_tiers_apart(self):
        from llm.accounting import Ledger, merge_reports

        def one(tier: str) -> dict:
            ledger = Ledger("r", Stage.DEVELOPER.value)
            ledger.record(
                purpose="heavy", provider="groq", model="m", estimated_tokens=10,
                reserved_tokens=20, actual_tokens=None, duration_seconds=0.1,
                outcome="success", tier=tier, difficulty=tier,
            )
            return ledger.report()

        merged = merge_reports(one("low"), one("high"))

        assert set(merged["by_tier"]) == {"low", "high"}

    def test_the_decision_reaches_the_run_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        _install_by_model(monkeypatch)
        reset_budgets()

        with caplog.at_level(logging.INFO, logger="llm.registry"):
            registry.get_structured_llm(
                _Widget(), Purpose.HEAVY, routed(), signal=TYPICAL
            )

        assert "medium tier, score 33" in caplog.text

    def test_no_credential_appears_in_a_route(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, pooled(2), TYPICAL)

        plan = plan_for(Purpose.HEAVY, TYPICAL, pooled(2), frozenset(ModelTier))
        serialised = str([record.__dict__ for record in ledger.records])

        assert SECRET not in serialised
        assert SECRET not in plan.describe()
        assert SECRET not in repr(plan)


# ── Nothing else moved ───────────────────────────────────────────


class TestDisabledIsTodaysBehaviour:
    def test_the_flag_is_off_by_default(self):
        assert get_settings().routing_enabled is False

    def test_model_resolution_is_untouched(self, monkeypatch: pytest.MonkeyPatch):
        built = _install_by_model(monkeypatch)
        reset_budgets()
        unrouted = Settings(llm_provider=LLMProvider.GROQ, groq_api_key=SECRET)

        registry.get_structured_llm(_Widget(), Purpose.HEAVY, unrouted, signal=TINY).invoke("go")

        # The HEAVY default, not the LOW tier the signal would have chosen.
        assert list(built) == [LARGE]

    def test_a_signal_is_ignored_entirely(self):
        settings = Settings(groq_api_key=SECRET)

        for signal in (TINY, TYPICAL, LARGE_SERVICE):
            assert plan_for(Purpose.HEAVY, signal, settings).routed is False

    def test_the_environment_flag_switches_it(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ROUTING_ENABLED", "true")
        monkeypatch.setenv("ROUTING_LOW_MAX_SCORE", "3")
        reset_settings_cache()

        assert get_settings().routing_enabled is True
        assert difficulty_for(TINY, get_settings()) is ModelTier.MEDIUM

    def test_the_account_pool_is_unaffected(self):
        settings = pooled(4)

        assert [a.id for a in settings.accounts_for(LLMProvider.GROQ)] == [
            "groq-1", "groq-2", "groq-3", "groq-4",
        ]

    def test_a_single_key_still_works(self, monkeypatch: pytest.MonkeyPatch):
        built = _install_by_model(monkeypatch)
        reset_budgets()

        registry.get_structured_llm(
            _Widget(), Purpose.HEAVY, routed(), signal=TINY
        ).invoke("go")

        assert built[SMALL].calls == 1

    def test_retry_classification_is_untouched(self):
        from llm.errors import Disposition, classify

        assert classify(RuntimeError("Error code: 429 - slow down")) is Disposition.RETRY
        assert classify(RuntimeError("Error code: 401 - bad key")) is Disposition.ABORT
        assert classify(RuntimeError("Error code: 413 - too large")) is Disposition.ABORT

    def test_budget_semantics_are_untouched(self):
        reset_budgets()
        settings = routed()

        assert _budget(settings, LARGE) is _budget(settings, LARGE)
        assert _budget(settings, LARGE) is not _budget(settings, SMALL)


def _Widget():
    """A schema for the structured ladder to satisfy."""
    from tests.test_structured import Widget

    return Widget
