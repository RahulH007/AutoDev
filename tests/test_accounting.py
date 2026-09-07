"""The cost and quality ledger: what every model attempt actually cost.

The ledger is an accountant, never a controller. :mod:`llm.budget` still decides
whether a call may proceed and still holds it back when the window is full;
nothing here may change what is called, in what order, or how often. So these
tests check two things at once — that spending is recorded faithfully, and that
recording it left the paced, retried, laddered call path exactly as it was.

Everything runs against fake chat models patched into the registry, so the real
metered ladder is built and exercised and no request leaves the process.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from agents.base import run_stage
from core.config import LLMProvider, Purpose, get_settings
from llm import registry
from llm.accounting import (
    CallRecord,
    Ledger,
    Outcome,
    current_ledger,
    merge_reports,
    outcome_for,
    record_attempt,
    recording,
)
from llm.budget import BudgetExceededError, budget_for, reset_budgets
from state.state import AgentStatus, Stage, initial_state
from tests.test_config_and_registry import MeteredFakeModel
from tests.test_structured import Widget

# Recognised by llm.errors.classify as transient, so the Retrier repeats it.
RATE_LIMITED = "Error code: 429 - {'message': 'rate limit reached'}"
# A 400 the model cannot satisfy: the ladder degrades to the next rung instead.
UNSATISFIABLE = "Error code: 400 - {'message': 'tool_use_failed'}"


class FlakyModel(Runnable):
    """Raises the queued exceptions in turn, then answers.

    Standing in for the provider rather than the registry, so a call really does
    travel through ``_metered`` and the retry wrapper above it.
    """

    def __init__(self, *failures: Exception, total_tokens: int | None = None) -> None:
        self.failures = list(failures)
        self.total_tokens = total_tokens
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        usage = (
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": self.total_tokens}
            if self.total_tokens is not None
            else None
        )
        return AIMessage(content="ok", usage_metadata=usage)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        await asyncio.sleep(0)  # yield, so concurrent runs really interleave
        return self.invoke(input, config)


class StructuredModel(Runnable):
    """A model whose structured rung returns a schema object, as providers do.

    The returned value carries no ``usage_metadata`` — that is the ordinary case
    for this pipeline, not an edge case, and the reason actual usage is so often
    unknown.
    """

    def __init__(self, *, failing: tuple[str, ...] = (), error: Exception | None = None) -> None:
        self.failing = failing
        self.error = error
        self.calls: list[str] = []

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls.append("text")
        return AIMessage(content='{"name": "sprocket", "size": 3}')

    def with_structured_output(
        self, schema: type, method: str | None = None, **kwargs: Any
    ) -> Runnable:
        from langchain_core.runnables import RunnableLambda

        key = method or "default"
        if key not in ("default", "json_schema"):
            raise NotImplementedError(f"{key} is not supported by this model")

        def run(_prompt: Any) -> Any:
            self.calls.append(key)
            if key in self.failing:
                raise self.error or RuntimeError(UNSATISFIABLE)
            return schema(name="sprocket", size=3)

        return RunnableLambda(run)


def _patch_model(monkeypatch: pytest.MonkeyPatch, model: Any) -> Any:
    monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)
    return model


def _text_budget():
    settings = get_settings()
    model = registry.model_name_for(LLMProvider.GOOGLE, Purpose.TEXT, settings)
    return budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)


# ── One record per attempt ───────────────────────────────────────


class TestOneRecordPerAttempt:
    def test_a_single_call_writes_a_single_record(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        assert len(ledger) == 1

    def test_the_record_names_the_run_stage_purpose_provider_and_model(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        entry = ledger.records[0]
        assert entry.run_id == "run-1"
        assert entry.stage == Stage.PM.value
        assert entry.purpose == Purpose.TEXT.value
        assert entry.provider == LLMProvider.GOOGLE.value
        # The same name the budget is keyed by, so the two always agree.
        assert entry.model == registry.model_name_for(
            LLMProvider.GOOGLE, Purpose.TEXT, get_settings()
        )

    def test_two_calls_write_two_records(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)
            registry.llm_call("hello again", Purpose.TEXT)

        assert ledger.totals()["calls"] == 2

    def test_the_reservation_is_recorded_as_the_budget_computed_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The ledger copies the budget's figure; it never re-estimates."""
        reset_budgets()
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=None))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        entry = ledger.records[0]
        # No usage was reported, so the window still holds the original claim.
        assert entry.reserved_tokens == _text_budget().used()
        # The prompt half is smaller than the whole claim, which adds the output
        # allowance the provider charges for whether or not it is used.
        assert 0 < entry.estimated_tokens < entry.reserved_tokens

    def test_a_call_made_outside_a_stage_is_simply_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Measurement must never be the reason a call fails."""
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        assert current_ledger() is None
        assert registry.llm_call("hello there", Purpose.TEXT) == "ok"
        assert record_attempt(purpose="text") is None


# ── Retries ──────────────────────────────────────────────────────


class TestRetriesAreRecordedSeparately:
    """Each attempt costs the provider's window, so each attempt is an entry."""

    def test_a_retried_call_writes_one_record_per_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        model = _patch_model(
            monkeypatch,
            FlakyModel(RuntimeError(RATE_LIMITED), RuntimeError(RATE_LIMITED), total_tokens=50),
        )

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        assert model.calls == 3
        assert ledger.totals()["calls"] == 3

    def test_each_attempt_carries_its_own_outcome(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(
            monkeypatch,
            FlakyModel(RuntimeError(RATE_LIMITED), RuntimeError(RATE_LIMITED), total_tokens=50),
        )

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        assert [entry.outcome for entry in ledger.records] == [
            Outcome.RETRY.value,
            Outcome.RETRY.value,
            Outcome.SUCCESS.value,
        ]

    def test_every_attempt_is_charged_not_just_the_last(self, monkeypatch: pytest.MonkeyPatch):
        """A failed attempt keeps its reservation, and the ledger says so too."""
        _patch_model(monkeypatch, FlakyModel(RuntimeError(RATE_LIMITED), total_tokens=50))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        totals = ledger.totals()
        assert totals["calls"] == 2
        assert totals["reserved_tokens"] == sum(e.reserved_tokens for e in ledger.records)

    def test_a_failure_that_exhausts_the_retries_records_every_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        failures = [RuntimeError(RATE_LIMITED) for _ in range(5)]
        _patch_model(monkeypatch, FlakyModel(*failures))

        with recording("run-1", Stage.PM.value) as ledger, pytest.raises(Exception, match="429"):
            registry.llm_call("hello there", Purpose.TEXT)

        # llm_max_retries is 3, so three attempts were made and all three recorded.
        assert ledger.totals()["calls"] == get_settings().llm_max_retries

    def test_a_degrading_error_records_the_rung_that_failed_and_the_one_that_worked(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Falling down the structured ladder is spending, and is accounted for."""
        model = _patch_model(monkeypatch, StructuredModel(failing=("default",)))

        with recording("run-1", Stage.PM.value) as ledger:
            result = registry.get_structured_llm(Widget, Purpose.STRUCTURED).invoke("go")

        assert isinstance(result, Widget)
        assert model.calls[:2] == ["default", "json_schema"]
        assert [entry.outcome for entry in ledger.records[:2]] == [
            Outcome.DEGRADE.value,
            Outcome.SUCCESS.value,
        ]


# ── Duration and outcome ─────────────────────────────────────────


class TestDurationAndOutcome:
    def test_a_successful_call_records_its_duration(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        assert ledger.records[0].duration_seconds >= 0.0
        assert ledger.totals()["seconds"] >= 0.0

    def test_a_successful_call_records_success(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        assert ledger.records[0].outcome == Outcome.SUCCESS.value

    def test_a_failed_call_is_recorded_with_its_duration(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, FlakyModel(RuntimeError("Error code: 401 - bad key")))

        with recording("run-1", Stage.PM.value) as ledger, pytest.raises(Exception, match="401"):
            registry.llm_call("hello there", Purpose.TEXT)

        assert len(ledger) == 1
        assert ledger.records[0].duration_seconds >= 0.0

    def test_a_failed_call_records_the_disposition_the_pipeline_acted_on(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_model(monkeypatch, FlakyModel(RuntimeError("Error code: 401 - bad key")))

        with recording("run-1", Stage.PM.value) as ledger, pytest.raises(Exception, match="401"):
            registry.llm_call("hello there", Purpose.TEXT)

        assert ledger.records[0].outcome == Outcome.ABORT.value

    def test_a_failed_call_records_no_usage(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, FlakyModel(RuntimeError("Error code: 401 - bad key")))

        with recording("run-1", Stage.PM.value) as ledger, pytest.raises(Exception, match="401"):
            registry.llm_call("hello there", Purpose.TEXT)

        assert ledger.records[0].actual_tokens is None

    def test_recording_a_failure_leaves_the_exception_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The observer must not swallow, wrap or reorder what the caller sees."""
        boom = RuntimeError("Error code: 401 - bad key")
        _patch_model(monkeypatch, FlakyModel(boom))

        with recording("run-1", Stage.PM.value), pytest.raises(RuntimeError) as caught:
            registry.llm_call("hello there", Purpose.TEXT)

        assert caught.value is boom

    def test_an_unknown_disposition_falls_back_rather_than_raising(self):
        assert outcome_for(RuntimeError("Error code: 401 - nope")) is Outcome.ABORT
        assert outcome_for(RuntimeError("something nobody classified")) is Outcome.DEGRADE


# ── Usage that the provider never reported ───────────────────────


class TestUnavailableUsage:
    """A structured response arrives with the provider's usage stripped off.

    That is the ordinary case here, so it must be represented as unknown rather
    than filled in with the estimate. Measuring is worthless if the figures
    cannot be told apart.
    """

    def test_a_response_without_usage_records_none(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=None))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        assert ledger.records[0].actual_tokens is None

    def test_the_estimate_is_not_promoted_to_actual_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=None))

        with recording("run-1", Stage.PM.value) as ledger:
            registry.llm_call("hello there", Purpose.TEXT)

        totals = ledger.totals()
        assert totals["actual_tokens"] is None
        assert totals["calls_with_usage"] == 0
        assert totals["reserved_tokens"] > 0

    def test_a_structured_call_does_not_crash_accounting(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A schema object has no ``usage_metadata`` attribute at all."""
        _patch_model(monkeypatch, StructuredModel())

        with recording("run-1", Stage.PM.value) as ledger:
            result = registry.get_structured_llm(Widget, Purpose.STRUCTURED).invoke("go")

        assert isinstance(result, Widget)
        assert len(ledger) == 1
        assert ledger.records[0].actual_tokens is None

    def test_a_mixed_run_totals_only_what_was_reported(self):
        ledger = Ledger("run-1", Stage.PM.value)
        _add(ledger, actual_tokens=300)
        _add(ledger, actual_tokens=None)

        totals = ledger.totals()
        assert totals["calls"] == 2
        assert totals["actual_tokens"] == 300
        assert totals["calls_with_usage"] == 1

    def test_cost_stays_unknown_because_there_is_no_pricing_source(self):
        ledger = Ledger("run-1", Stage.PM.value)
        _add(ledger, actual_tokens=300)

        assert ledger.totals()["estimated_cost"] is None


# ── What is not an attempt ───────────────────────────────────────


class TestABudgetRefusalIsNotAnAttempt:
    def test_a_call_too_large_for_the_window_records_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """No model was asked anything, so there is nothing to account for."""
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "10")
        from core.config import reset_settings_cache

        reset_settings_cache()
        model = _patch_model(monkeypatch, MeteredFakeModel(total_tokens=120))

        with recording("run-1", Stage.PM.value) as ledger, pytest.raises(BudgetExceededError):
            registry.llm_call("hello there", Purpose.TEXT)

        assert model.calls == 0
        assert len(ledger) == 0


# ── Isolation between concurrent runs ────────────────────────────


class TestConcurrentRunsStaySeparate:
    """The server drives each run in its own asyncio task.

    A task inherits a copy of the context at creation, so a ledger bound inside
    one run is invisible to another however the two interleave. Nothing here is
    process-global, which is the whole reason this holds.
    """

    @staticmethod
    async def _stage(run_id: str, stage: Stage, calls: int) -> dict[str, Any]:
        state = {**initial_state(run_id, "build something"), "run_id": run_id}

        async def body() -> dict[str, Any]:
            for _ in range(calls):
                await registry.allm_call("hello there", Purpose.TEXT)
            return {}

        return await run_stage(state, stage, body)

    async def test_neither_run_sees_the_other_calls(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))

        first, second = await asyncio.gather(
            self._stage("run-a", Stage.PM, calls=1),
            self._stage("run-b", Stage.ARCHITECTURE, calls=3),
        )

        assert first["cost_report"]["calls"] == 1
        assert second["cost_report"]["calls"] == 3

    async def test_neither_run_sees_the_other_stage(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))

        first, second = await asyncio.gather(
            self._stage("run-a", Stage.PM, calls=1),
            self._stage("run-b", Stage.ARCHITECTURE, calls=3),
        )

        assert list(first["cost_report"]["by_stage"]) == [Stage.PM.value]
        assert list(second["cost_report"]["by_stage"]) == [Stage.ARCHITECTURE.value]

    async def test_the_ledger_is_unbound_once_the_stage_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))

        await self._stage("run-a", Stage.PM, calls=1)

        assert current_ledger() is None


# ── The stage boundary ───────────────────────────────────────────


class TestRunStageAccounting:
    @staticmethod
    async def _run(state: dict[str, Any], stage: Stage, body: Any) -> dict[str, Any]:
        return await run_stage(state, stage, body)

    async def test_a_stage_reports_what_it_spent(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))
        state = initial_state("run-1", "build something")

        async def body() -> dict[str, Any]:
            await registry.allm_call("hello there", Purpose.TEXT)
            return {"prd": {"product_name": "x"}}

        update = await self._run(state, Stage.PM, body)

        assert update["cost_report"]["calls"] == 1
        assert update["prd"] == {"product_name": "x"}

    async def test_a_failed_stage_still_reports_what_it_spent(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The calls it made before it fell over were paid for either way."""
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))
        state = initial_state("run-1", "build something")

        async def body() -> dict[str, Any]:
            await registry.allm_call("hello there", Purpose.TEXT)
            raise RuntimeError("the stage fell over afterwards")

        update = await self._run(state, Stage.PM, body)

        assert update["status"][Stage.PM.value] == AgentStatus.FAILED.value
        assert update["cost_report"]["calls"] == 1

    async def test_a_stage_that_calls_nothing_reports_nothing_spent(self):
        state = initial_state("run-1", "build something")

        update = await self._run(state, Stage.STATIC_GATE, _nothing)

        assert update["cost_report"]["calls"] == 0
        assert update["cost_report"]["actual_tokens"] is None

    async def test_spending_accumulates_across_stages(self, monkeypatch: pytest.MonkeyPatch):
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))
        state: dict[str, Any] = initial_state("run-1", "build something")

        async def body() -> dict[str, Any]:
            await registry.allm_call("hello there", Purpose.TEXT)
            return {}

        first = await self._run(state, Stage.PM, body)
        second = await self._run({**state, **first}, Stage.ARCHITECTURE, body)

        assert second["cost_report"]["calls"] == 2
        assert set(second["cost_report"]["by_stage"]) == {
            Stage.PM.value,
            Stage.ARCHITECTURE.value,
        }

    async def test_a_retried_stage_adds_to_its_own_stage_total(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))
        state: dict[str, Any] = initial_state("run-1", "build something")

        async def body() -> dict[str, Any]:
            await registry.allm_call("hello there", Purpose.TEXT)
            return {}

        first = await self._run(state, Stage.DEVELOPER, body)
        second = await self._run({**state, **first}, Stage.DEVELOPER, body)

        assert second["cost_report"]["by_stage"][Stage.DEVELOPER.value]["calls"] == 2


# ── State written before this feature existed ────────────────────


class TestOlderStateStillLoads:
    def test_initial_state_seeds_the_field(self):
        assert initial_state("run-1", "build something")["cost_report"] == {}

    async def test_a_checkpoint_without_the_field_resumes(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A run interrupted before this release has no cost_report at all."""
        _patch_model(monkeypatch, FlakyModel(total_tokens=None))
        older = initial_state("run-1", "build something")
        older.pop("cost_report")

        async def body() -> dict[str, Any]:
            await registry.allm_call("hello there", Purpose.TEXT)
            return {}

        update = await run_stage(older, Stage.PM, body)

        assert update["cost_report"]["calls"] == 1

    async def test_a_checkpoint_with_the_field_set_to_none_resumes(self):
        older: dict[str, Any] = {**initial_state("run-1", "build something"), "cost_report": None}

        update = await run_stage(older, Stage.STATIC_GATE, _nothing)

        assert update["cost_report"]["calls"] == 0


# ── Aggregation ──────────────────────────────────────────────────


class TestGrouping:
    @staticmethod
    def _ledger() -> Ledger:
        ledger = Ledger("run-1", Stage.PM.value)
        _add(ledger, stage=Stage.PM.value, model="flash", actual_tokens=100)
        _add(ledger, stage=Stage.DEVELOPER.value, model="pro", actual_tokens=None)
        _add(ledger, stage=Stage.DEVELOPER.value, model="pro", actual_tokens=250)
        return ledger

    def test_totals_by_stage(self):
        by_stage = self._ledger().by_stage()

        assert by_stage[Stage.PM.value]["calls"] == 1
        assert by_stage[Stage.DEVELOPER.value]["calls"] == 2
        assert by_stage[Stage.DEVELOPER.value]["actual_tokens"] == 250

    def test_totals_by_provider_and_model(self):
        by_model = self._ledger().by_model()

        assert by_model["google:flash"]["calls"] == 1
        assert by_model["google:pro"]["calls"] == 2

    def test_totals_by_run(self):
        ledger = Ledger("run-1", Stage.PM.value)
        _add(ledger)
        _add(ledger, run_id="run-2")

        assert set(ledger.by_run()) == {"run-1", "run-2"}

    def test_totals_by_purpose(self):
        ledger = Ledger("run-1", Stage.PM.value)
        _add(ledger, purpose=Purpose.HEAVY.value)
        _add(ledger, purpose=Purpose.CHEAP.value)

        assert set(ledger.by_purpose()) == {Purpose.HEAVY.value, Purpose.CHEAP.value}

    def test_the_report_carries_the_totals_and_both_breakdowns(self):
        report = self._ledger().report()

        assert report["calls"] == 3
        assert set(report["by_stage"]) == {Stage.PM.value, Stage.DEVELOPER.value}
        assert set(report["by_model"]) == {"google:flash", "google:pro"}

    def test_outcomes_are_counted(self):
        ledger = Ledger("run-1", Stage.PM.value)
        _add(ledger, outcome=Outcome.SUCCESS)
        _add(ledger, outcome=Outcome.RETRY)
        _add(ledger, outcome=Outcome.RETRY)

        assert ledger.totals()["outcomes"] == {"success": 1, "retry": 2}


class TestMergingReports:
    def test_merging_two_empty_reports_is_well_formed(self):
        merged = merge_reports({}, {})

        assert merged["calls"] == 0
        assert merged["actual_tokens"] is None
        assert merged["by_stage"] == {}

    def test_counts_add_up(self):
        left = Ledger("r", Stage.PM.value)
        _add(left, actual_tokens=10)
        right = Ledger("r", Stage.QA.value)
        _add(right, actual_tokens=20)

        merged = merge_reports(left.report(), right.report())

        assert merged["calls"] == 2
        assert merged["actual_tokens"] == 30

    def test_unavailable_usage_on_one_side_does_not_zero_the_other(self):
        left = Ledger("r", Stage.PM.value)
        _add(left, actual_tokens=10)
        right = Ledger("r", Stage.QA.value)
        _add(right, actual_tokens=None)

        merged = merge_reports(left.report(), right.report())

        assert merged["actual_tokens"] == 10
        assert merged["calls_with_usage"] == 1

    def test_unavailable_on_both_sides_stays_unavailable(self):
        left = Ledger("r", Stage.PM.value)
        _add(left, actual_tokens=None)
        right = Ledger("r", Stage.QA.value)
        _add(right, actual_tokens=None)

        assert merge_reports(left.report(), right.report())["actual_tokens"] is None

    def test_the_same_stage_on_both_sides_is_combined(self):
        left = Ledger("r", Stage.DEVELOPER.value)
        _add(left, stage=Stage.DEVELOPER.value)
        right = Ledger("r", Stage.DEVELOPER.value)
        _add(right, stage=Stage.DEVELOPER.value)

        merged = merge_reports(left.report(), right.report())

        assert merged["by_stage"][Stage.DEVELOPER.value]["calls"] == 2

    def test_a_breakdown_carries_no_nested_breakdowns(self):
        left = Ledger("r", Stage.PM.value)
        _add(left)

        merged = merge_reports({}, left.report())

        assert "by_stage" not in merged["by_stage"][Stage.PM.value]


# ── The paced, retried call path is unchanged ────────────────────


class TestExistingBehaviourIsUnchanged:
    """Observation must be invisible to everything it observes."""

    def test_the_budget_still_settles_against_reported_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=777))

        with recording("run-1", Stage.PM.value):
            registry.llm_call("hello there", Purpose.TEXT)

        assert _text_budget().used() == 777

    def test_the_budget_still_keeps_the_estimate_when_usage_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        _patch_model(monkeypatch, MeteredFakeModel(total_tokens=None))

        with recording("run-1", Stage.PM.value):
            registry.llm_call("hello there", Purpose.TEXT)

        assert _text_budget().used() >= get_settings().max_output_for(Purpose.TEXT)

    def test_a_failed_attempt_still_keeps_its_reservation(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        _patch_model(monkeypatch, FlakyModel(RuntimeError("Error code: 401 - bad key")))

        with recording("run-1", Stage.PM.value), pytest.raises(Exception, match="401"):
            registry.llm_call("hello there", Purpose.TEXT)

        assert _text_budget().used() > 0

    def test_the_model_cache_still_returns_one_client_per_key(self):
        first = registry.get_chat_model(Purpose.TEXT, LLMProvider.GOOGLE)
        second = registry.get_chat_model(Purpose.TEXT, LLMProvider.GOOGLE)

        assert first is second

    def test_the_retrier_still_stops_at_a_non_transient_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        model = _patch_model(
            monkeypatch, FlakyModel(*[RuntimeError("Error code: 401 - bad key")] * 5)
        )

        with recording("run-1", Stage.PM.value), pytest.raises(Exception, match="401"):
            registry.llm_call("hello there", Purpose.TEXT)

        assert model.calls == 1

    def test_the_ladder_still_degrades_to_the_next_rung(self, monkeypatch: pytest.MonkeyPatch):
        model = _patch_model(monkeypatch, StructuredModel(failing=("default",)))

        with recording("run-1", Stage.PM.value):
            assert isinstance(
                registry.get_structured_llm(Widget, Purpose.STRUCTURED).invoke("go"), Widget
            )

        assert model.calls[:2] == ["default", "json_schema"]

    def test_the_ceiling_a_purpose_reserves_is_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        """Adaptive ceilings are a later phase; these figures must not move."""
        settings = get_settings()

        assert settings.max_output_for(Purpose.HEAVY) == 4096
        assert settings.max_output_for(Purpose.STRUCTURED) == 3500
        assert settings.max_output_for(Purpose.TEXT) == 2000
        assert settings.max_output_for(Purpose.CHEAP) == 1000


# ── Helpers ──────────────────────────────────────────────────────


async def _nothing() -> dict[str, Any]:
    return {}


def _add(ledger: Ledger, **overrides: Any) -> CallRecord:
    fields: dict[str, Any] = {
        "purpose": Purpose.TEXT.value,
        "provider": LLMProvider.GOOGLE.value,
        "model": "flash",
        "estimated_tokens": 40,
        "reserved_tokens": 2040,
        "actual_tokens": None,
        "duration_seconds": 0.5,
        "outcome": Outcome.SUCCESS,
    }
    fields.update(overrides)
    return ledger.record(**fields)
