"""Paying for a stronger model, but only when the evidence earns it.

Difficulty routing picks a tier before a service is written. Sometimes it picks
too small: the code does not compile, or does not match the architecture, or
fails its own tests. That is the one situation where spending more is the right
answer, and this is the rule that decides it.

The rule has to be conservative in a specific direction. A false negative costs
one more ordinary attempt; a false positive spends a more expensive model on a
problem it cannot fix — a rate limit, an exhausted account, a rejected key, a
harness that never started. So the classifier says *why* it is escalating, and
refuses everything that is really a resource or infrastructure problem.

Two things escalation deliberately does not do: it does not touch the graph's
retry budget, and it does not add a single model call. It changes which model a
call uses, and nothing else.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from agents.attribution import (
    SOURCE_QA,
    SOURCE_RUNNER,
    SOURCE_STATIC,
    SOURCE_TEST,
    Failure,
    escalation_reason,
    justifies_escalation,
)
from agents.developer_agent import developer_agent
from core.config import LLMProvider, Purpose, Settings, get_settings
from core.contracts import CHECK_NAME as CONTRACT_CHECK
from core.contracts import derive_contract
from llm import registry
from llm.accounting import recording
from llm.budget import BudgetExceededError, budget_for, reset_budgets
from llm.routing import ModelTier, RoutePlan, RouteSignal, escalate, next_tier, plan_for
from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode
from state.state import AgentStatus, Stage

SECRET = "sk-groq-never-log-me"
SMALL, LARGE = "openai/gpt-oss-20b", "openai/gpt-oss-120b"

SERVICE, SLUG = "Reports", "reports"

# A service small enough to start at LOW: 2*2 + 1 + 1 = 6.
TINY = RouteSignal(key_files=2, endpoints=1, data_models=1)
# 16 + 12 + 5 = 33, comfortably MEDIUM.
TYPICAL = RouteSignal(key_files=8, endpoints=12, data_models=5)


def routed(**overrides: Any) -> Settings:
    return Settings(
        llm_provider=LLMProvider.GROQ,
        groq_api_key=SECRET,
        routing_enabled=True,
        **overrides,
    )


# ── What counts as evidence ──────────────────────────────────────


def compile_failure(service: str = SLUG) -> Failure:
    return Failure(
        source=SOURCE_STATIC, service=service, check="compile",
        text=f"{service}/app/main.py:12: invalid syntax",
    )


def contract_failure(service: str = SLUG) -> Failure:
    return Failure(
        source=SOURCE_STATIC, service=service, check=CONTRACT_CHECK,
        text=f"{service}/app/store.py: the architecture requires this key file",
    )


def failed_test(service: str = SLUG) -> Failure:
    return Failure(
        source=SOURCE_TEST, service=service,
        text="test_login: AssertionError: expected 200, got 500",
    )


def bug(severity: str, service: str = SLUG) -> Failure:
    return Failure(
        source=SOURCE_QA, service=service, severity=severity,
        text=f"[{severity.upper()}] app/auth.py: passwords stored in plaintext",
    )


def runner_failure(service: str = SLUG) -> Failure:
    return Failure(
        source=SOURCE_RUNNER, service=service,
        text="Dependency install failed: npm not found.",
    )


class TestEscalationWorthyEvidence:
    def test_code_that_does_not_compile_escalates(self):
        assert escalation_reason([compile_failure()]) == "failed to compile"

    def test_a_contract_violation_escalates(self):
        reason = escalation_reason([contract_failure()])

        assert reason is not None
        assert "approved architecture" in reason

    def test_a_real_test_failure_escalates(self):
        assert escalation_reason([failed_test()]) == "1 failing test(s)"

    def test_a_serious_reviewed_bug_escalates(self):
        assert escalation_reason([bug("critical")]) == "1 serious bug(s) reported"
        assert escalation_reason([bug("major")]) == "1 serious bug(s) reported"

    def test_the_hardest_fact_available_is_the_reason_given(self):
        """Compiler output outranks an opinion, as it does everywhere else."""
        reason = escalation_reason([bug("critical"), failed_test(), compile_failure()])

        assert reason == "failed to compile"

    def test_the_count_is_reported(self):
        assert escalation_reason([failed_test(), failed_test()]) == "2 failing test(s)"

    def test_no_evidence_is_no_escalation(self):
        assert escalation_reason([]) is None


class TestNotEscalationWorthy:
    def test_a_harness_that_never_started_is_not_evidence(self):
        """The code was never executed, so nothing was learned about it."""
        assert escalation_reason([runner_failure()]) is None

    def test_a_minor_review_note_is_not_evidence(self):
        assert escalation_reason([bug("minor")]) is None

    def test_an_unrated_bug_is_not_evidence(self):
        assert escalation_reason([Failure(source=SOURCE_QA, service=SLUG, text="tidier?")]) is None

    def test_a_minor_note_beside_a_harness_error_still_escalates_nothing(self):
        assert escalation_reason([runner_failure(), bug("minor")]) is None


class TestProviderFailuresNeverEscalate:
    """A failed call describes the provider, never the code it did not produce.

    `llm.errors.classify` already sorts every one of these and not one of its
    dispositions is about generated code, which is why none of them is an input
    to the escalation decision at all.
    """

    def test_a_rate_limit_does_not_escalate(self):
        assert justifies_escalation(RuntimeError("Error code: 429 - rate limit reached")) is False

    def test_an_exhausted_account_does_not_escalate(self):
        exhausted = RuntimeError(
            "Error code: 429 - on tokens per minute (TPM): Limit 8000, Used 8000, Requested 500"
        )
        assert justifies_escalation(exhausted) is False

    def test_an_authentication_failure_does_not_escalate(self):
        assert justifies_escalation(RuntimeError("Error code: 401 - invalid api key")) is False

    def test_a_budget_refusal_does_not_escalate(self):
        assert justifies_escalation(BudgetExceededError("a single call is too large")) is False

    def test_a_transient_http_failure_does_not_escalate(self):
        assert justifies_escalation(RuntimeError("Error code: 503 - upstream unavailable")) is False
        assert justifies_escalation(TimeoutError("the read timed out")) is False

    def test_a_request_too_large_does_not_escalate(self):
        """A ceiling that was too small is Phase 4's problem, not the model's."""
        assert justifies_escalation(RuntimeError("Error code: 413 - request too large")) is False

    def test_the_existing_classifier_is_untouched(self):
        from llm.errors import Disposition, classify

        assert classify(RuntimeError("Error code: 429 - slow down")) is Disposition.RETRY
        assert classify(RuntimeError("Error code: 401 - bad key")) is Disposition.ABORT
        assert classify(RuntimeError("Error code: 413 - too large")) is Disposition.ABORT


# ── The ladder ───────────────────────────────────────────────────


class TestTheLadder:
    def test_it_moves_one_step_at_a_time(self):
        assert next_tier(ModelTier.LOW) is ModelTier.MEDIUM
        assert next_tier(ModelTier.MEDIUM) is ModelTier.HIGH

    def test_the_top_has_nowhere_to_go(self):
        assert next_tier(ModelTier.HIGH) is None

    def test_one_escalation_does_not_skip_a_tier(self):
        assert escalate(ModelTier.LOW, 1) == (ModelTier.MEDIUM, 1)

    def test_two_escalations_climb_one_at_a_time(self):
        assert escalate(ModelTier.LOW, 2) == (ModelTier.HIGH, 2)

    def test_climbing_past_the_top_reports_what_actually_applied(self):
        """So nothing downstream reads HIGH as having been upgraded when it was not."""
        assert escalate(ModelTier.HIGH, 1) == (ModelTier.HIGH, 0)
        assert escalate(ModelTier.MEDIUM, 5) == (ModelTier.HIGH, 1)

    def test_it_never_moves_downward(self):
        for tier in ModelTier:
            climbed, _ = escalate(tier, 1)
            assert list(ModelTier).index(climbed) >= list(ModelTier).index(tier)


class TestPlanning:
    def test_an_escalated_signal_raises_the_tier(self):
        plan = plan_for(Purpose.HEAVY, RouteSignal(**{**TINY.__dict__, "escalations": 1}), routed())

        assert plan.difficulty is ModelTier.LOW
        assert plan.tier is ModelTier.MEDIUM
        assert plan.escalated is True

    def test_a_medium_task_escalates_to_high(self):
        signal = RouteSignal(**{**TYPICAL.__dict__, "escalations": 1})

        assert plan_for(Purpose.HEAVY, signal, routed()).tier is ModelTier.HIGH

    def test_a_high_task_does_not_escalate(self):
        signal = RouteSignal(key_files=30, endpoints=40, escalations=1)
        plan = plan_for(Purpose.HEAVY, signal, routed())

        assert plan.difficulty is ModelTier.HIGH
        assert plan.tier is ModelTier.HIGH
        assert plan.escalated is False

    def test_the_reason_travels_with_the_plan(self):
        signal = RouteSignal(
            **{**TINY.__dict__, "escalations": 1, "escalation_reason": "failed to compile"}
        )
        plan = plan_for(Purpose.HEAVY, signal, routed())

        assert plan.escalation_reason == "failed to compile"
        assert "escalated from low: failed to compile" in plan.describe()

    def test_an_escalated_tier_is_not_stepped_back_down(self):
        """Re-running the model just judged inadequate would be the one wrong answer."""
        signal = RouteSignal(**{**TINY.__dict__, "escalations": 1})

        plan = plan_for(Purpose.HEAVY, signal, routed(), frozenset({ModelTier.LOW}))

        assert plan.tier is ModelTier.MEDIUM

    def test_escalation_does_not_change_the_difficulty_score(self):
        plain = plan_for(Purpose.HEAVY, TINY, routed())
        raised = plan_for(
            Purpose.HEAVY, RouteSignal(**{**TINY.__dict__, "escalations": 1}), routed()
        )

        assert plain.score == raised.score == 6

    def test_escalation_is_inert_while_routing_is_off(self):
        signal = RouteSignal(**{**TINY.__dict__, "escalations": 2})

        assert plan_for(Purpose.HEAVY, signal, Settings(groq_api_key=SECRET)).routed is False

    def test_an_unroutable_purpose_cannot_be_escalated(self):
        signal = RouteSignal(**{**TINY.__dict__, "escalations": 1})

        for purpose in (Purpose.STRUCTURED, Purpose.TEXT, Purpose.CHEAP):
            assert plan_for(purpose, signal, routed()).routed is False

    def test_a_plan_is_still_immutable(self):
        import dataclasses

        plan = plan_for(Purpose.HEAVY, TINY, routed())

        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.escalations = 3  # type: ignore[misc]


# ── Through the developer stage ──────────────────────────────────


ARCHITECTURE = {
    "architecture_style": "modular_monolith",
    "services": [
        {
            "name": SERVICE,
            "description": "Reporting.",
            "tech_stack": ["Python"],
            "dependencies": [],
            "api_endpoints": [{"method": "GET", "path": "/reports"}],
            "data_models": [{"name": "Report", "description": "r", "fields": []}],
        }
    ],
    "project_structure": [
        {"service_name": SERVICE, "folders": ["app"], "key_files": ["app/main.py", "app/r.py"]}
    ],
}


def _static(service: str = SLUG, check: str = "compile") -> dict[str, Any]:
    line = f"{service}/app/main.py:12: invalid syntax"
    return {
        "ran": True,
        "passed": False,
        "checks": [{"name": check, "service": service, "failures": [line]}],
        # `run_static_gate` writes both, and `build_failure_evidence` reads this
        # one -- so a fixture without it would never look like a fix pass.
        "failures": [line],
    }


def _verification(service: str = SLUG, *, runner_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"service": service, "ran": True, "failures": [], "error": ""}
    if runner_error:
        result["error"] = "Dependency install failed."
    else:
        result["failures"] = [{"test": "test_x", "message": "AssertionError: boom"}]
    return {"ran": True, "passed": False, "services": [result]}


def _qa(severity: str = "critical") -> dict[str, Any]:
    return {
        "service_reports": [
            {
                "service_name": SERVICE,
                "code_quality_score": 4,
                "bugs": [
                    {
                        "file_path": "app/main.py",
                        "line_number": "1",
                        "severity": severity,
                        "description": "Something serious.",
                        "suggested_fix": "Fix it.",
                    }
                ],
            }
        ]
    }


def _manifest() -> dict[str, Any]:
    from core import manifest as manifest_util

    built: dict[str, Any] = {}
    manifest_util.add_file(built, SERVICE, "app/main.py")
    return built


class RecordingRegistry:
    """Captures the routing signal each service call was built with."""

    def __init__(self) -> None:
        self.signals: list[RouteSignal | None] = []
        self.plans: list[RoutePlan] = []
        self.calls = 0

    def install(self, monkeypatch: pytest.MonkeyPatch, settings: Settings):
        outer = self

        class Model:
            async def ainvoke(self, prompt: Any, config: Any = None) -> Any:
                outer.calls += 1
                return DeveloperSchema(
                    project_name="P",
                    services=[
                        ServiceCode(
                            service_name=SERVICE,
                            files=[
                                CodeFile(
                                    file_path="app/main.py", file_name="main.py",
                                    language="python", code="x = 1\n", description="m",
                                )
                            ],
                        )
                    ],
                )

        def fake(schema, purpose=None, _settings=None, demand=None, signal=None, **_):
            outer.signals.append(signal)
            outer.plans.append(plan_for(purpose, signal, settings))
            return Model()

        monkeypatch.setattr(registry, "get_structured_llm", fake)
        return self

    @property
    def tiers(self) -> list[ModelTier | None]:
        return [plan.tier for plan in self.plans]


@pytest.fixture
def state(workspace):
    return {
        "run_id": workspace.run_id,
        "user_requirements": "Build it.",
        "prd": {},
        "architecture": ARCHITECTURE,
        "contract": derive_contract(ARCHITECTURE).model_dump(mode="json"),
        "code_manifest": _manifest(),
        "status": {stage.value: AgentStatus.PENDING.value for stage in Stage},
        "retry_count": 1,
        "generated_services": [SLUG],
    }


@pytest.fixture
def routing_on(monkeypatch: pytest.MonkeyPatch):
    from core.config import reset_settings_cache

    monkeypatch.setenv("ROUTING_ENABLED", "true")
    reset_settings_cache()
    yield
    reset_settings_cache()


class TestEscalationThroughTheDeveloper:
    async def test_a_compile_failure_escalates_the_affected_service(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent({**state, "static_report": _static()})

        assert update["service_escalations"] == {SLUG: 1}
        assert captured.tiers == [ModelTier.MEDIUM]

    async def test_a_test_failure_escalates(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        await developer_agent({**state, "verification_report": _verification()})

        assert captured.tiers == [ModelTier.MEDIUM]

    async def test_a_serious_bug_escalates(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        await developer_agent({**state, "qa_report": _qa("critical")})

        assert captured.tiers == [ModelTier.MEDIUM]

    async def test_a_contract_violation_escalates(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        await developer_agent({**state, "static_report": _static(check=CONTRACT_CHECK)})

        assert captured.tiers == [ModelTier.MEDIUM]

    async def test_a_first_pass_never_escalates(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        """No evidence exists yet, so there is nothing to respond to."""
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent({**state, "generated_services": []})

        assert update["service_escalations"] == {}
        assert captured.tiers == [ModelTier.LOW]

    async def test_a_runner_error_does_not_escalate(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent(
            {**state, "verification_report": _verification(runner_error=True)}
        )

        assert update["service_escalations"] == {}
        assert captured.tiers == [ModelTier.LOW]

    async def test_a_minor_bug_does_not_escalate(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        await developer_agent({**state, "qa_report": _qa("minor")})

        assert captured.tiers == [ModelTier.LOW]

    async def test_only_the_affected_service_is_escalated(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        """Another service's compiler errors are not evidence about this one."""
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent(
            {**state, "static_report": _static(service="something-else")}
        )

        assert update["service_escalations"] == {}
        assert captured.tiers == [ModelTier.LOW]

    async def test_the_decision_reaches_the_run_log(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        RecordingRegistry().install(monkeypatch, get_settings())

        with caplog.at_level(logging.INFO, logger="agents.developer_agent"):
            await developer_agent({**state, "static_report": _static()})

        assert "Escalating reports to a stronger model: failed to compile" in caplog.text

    async def test_no_credential_appears_in_the_escalation_state(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        RecordingRegistry().install(monkeypatch, get_settings())

        with caplog.at_level(logging.DEBUG):
            update = await developer_agent({**state, "static_report": _static()})

        assert SECRET not in str(update["service_escalations"])
        assert SECRET not in caplog.text


class TestTheCap:
    async def test_a_service_escalates_only_once_by_default(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())
        failing = {**state, "static_report": _static()}

        first = await developer_agent(failing)
        # The gate finds the same problem again: evidence re-injected, because a
        # successful pass clears it and the service would otherwise be skipped.
        second = await developer_agent(
            {**failing, **first, "static_report": _static(), "generated_services": []}
        )

        assert first["service_escalations"] == {SLUG: 1}
        assert second["service_escalations"] == {SLUG: 1}
        assert captured.tiers == [ModelTier.MEDIUM, ModelTier.MEDIUM]

    async def test_an_earlier_escalation_is_not_forgotten(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        """It stays upgraded even on a pass whose evidence has cleared."""
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        await developer_agent(
            {**state, "service_escalations": {SLUG: 1}, "generated_services": []}
        )

        assert captured.tiers == [ModelTier.MEDIUM]

    async def test_the_cap_is_configurable(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        from core.config import reset_settings_cache

        monkeypatch.setenv("MAX_MODEL_ESCALATIONS", "2")
        reset_settings_cache()
        captured = RecordingRegistry().install(monkeypatch, get_settings())
        failing = {**state, "static_report": _static()}

        first = await developer_agent(failing)
        second = await developer_agent(
            {**failing, **first, "static_report": _static(), "generated_services": []}
        )

        assert second["service_escalations"] == {SLUG: 2}
        assert captured.tiers == [ModelTier.MEDIUM, ModelTier.HIGH]

    async def test_a_spent_escalation_survives_a_failed_pass(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        """Otherwise the next pass would buy the same upgrade again."""

        def failing(schema, purpose=None, _settings=None, demand=None, signal=None, **_):
            class Model:
                async def ainvoke(self, prompt, config=None):
                    raise RuntimeError("the provider is down")

            return Model()

        monkeypatch.setattr(registry, "get_structured_llm", failing)

        update = await developer_agent({**state, "static_report": _static()})

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value
        assert update["service_escalations"] == {SLUG: 1}

    def test_the_default_is_one(self):
        assert get_settings().max_model_escalations == 1


class TestItDoesNotDisturbTheRetryMachinery:
    async def test_the_retry_count_is_untouched_by_escalation(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        """Escalation counts model upgrades; retry_count counts developer passes."""
        RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent({**state, "static_report": _static()})

        assert update["retry_count"] == 2  # one more pass, exactly as before

    async def test_escalation_adds_no_model_call(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        await developer_agent({**state, "static_report": _static()})

        assert captured.calls == 1

    async def test_the_existing_per_service_state_is_intact(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent({**state, "static_report": _static()})

        assert update["generated_services"] == [SLUG]
        assert update["failed_services"] == []
        assert update["service_failures"] == {}

    async def test_state_written_before_the_field_existed_still_runs(
        self, state, routing_on, monkeypatch: pytest.MonkeyPatch
    ):
        RecordingRegistry().install(monkeypatch, get_settings())
        older = {k: v for k, v in state.items() if k != "service_escalations"}

        update = await developer_agent({**older, "static_report": _static()})

        assert update["service_escalations"] == {SLUG: 1}

    def test_initial_state_seeds_the_field(self):
        from state.state import initial_state

        assert initial_state("run-1", "build it")["service_escalations"] == {}


class TestRoutingDisabled:
    async def test_nothing_escalates(self, state, monkeypatch: pytest.MonkeyPatch):
        captured = RecordingRegistry().install(monkeypatch, get_settings())

        update = await developer_agent({**state, "static_report": _static()})

        assert update["service_escalations"] == {}
        assert captured.tiers == [None]

    async def test_the_model_is_the_one_it_always_was(
        self, state, monkeypatch: pytest.MonkeyPatch
    ):
        built: dict[str, int] = {}

        def build(purpose, provider, settings=None, ceiling=None, account=None, tier=None):
            name = registry.model_name_for(provider, purpose, settings, tier)
            built[name] = built.get(name, 0) + 1

            class Chat:
                def with_structured_output(self, schema, method=None, **kwargs):
                    from langchain_core.runnables import RunnableLambda

                    if (method or "default") != "default":
                        raise NotImplementedError(method)
                    return RunnableLambda(lambda _p: _developer_output())

                async def ainvoke(self, prompt, config=None, **kwargs):
                    return AIMessage(content="text")

            return Chat()

        monkeypatch.setattr(registry, "get_chat_model", build)
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", SECRET)
        from core.config import reset_settings_cache

        reset_settings_cache()
        reset_budgets()

        await developer_agent({**state, "static_report": _static()})

        assert list(built) == [LARGE]  # the HEAVY default, not a tier model


# ── Account pool and ceilings ────────────────────────────────────


class TierAwareChat(Runnable):
    def __init__(self, model: str, account: str | None) -> None:
        self.model = model
        self.account = account
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        return AIMessage(content=self.model)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        return self.invoke(input, config)

    def with_structured_output(self, schema: type, method: str | None = None, **kwargs: Any):
        from langchain_core.runnables import RunnableLambda

        if (method or "default") != "default":
            raise NotImplementedError(method)

        def run(_prompt: Any) -> Any:
            self.calls += 1
            return _developer_output()

        return RunnableLambda(run)


def _developer_output() -> DeveloperSchema:
    return DeveloperSchema(
        project_name="P",
        services=[
            ServiceCode(
                service_name=SERVICE,
                files=[
                    CodeFile(
                        file_path="app/main.py", file_name="main.py",
                        language="python", code="x = 1\n", description="m",
                    )
                ],
            )
        ],
    )


class TestEscalationUsesTheExistingPool:
    @staticmethod
    def _install(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str | None], TierAwareChat]:
        clients: dict[tuple[str, str | None], TierAwareChat] = {}

        def build(purpose, provider, settings=None, ceiling=None, account=None, tier=None):
            name = registry.model_name_for(provider, purpose, settings, tier)
            key = (name, account.id if account else None)
            return clients.setdefault(key, TierAwareChat(name, key[1]))

        monkeypatch.setattr(registry, "get_chat_model", build)
        return clients

    def _pooled_env(self, monkeypatch: pytest.MonkeyPatch, **extra: str):
        from core.config import reset_settings_cache

        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("ROUTING_ENABLED", "true")
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        for index in (1, 2):
            monkeypatch.setenv(f"GROQ_API_KEY_{index}", f"{SECRET}-{index}")
        for name, value in extra.items():
            monkeypatch.setenv(name, value)
        reset_settings_cache()
        reset_budgets()

    async def test_the_escalated_tier_goes_through_the_account_pool(
        self, state, monkeypatch: pytest.MonkeyPatch
    ):
        self._pooled_env(monkeypatch)
        clients = self._install(monkeypatch)

        await developer_agent({**state, "static_report": _static()})

        # The stronger model, chosen by an account from the pool.
        served = [key for key, client in clients.items() if client.calls]
        assert served == [(LARGE, "groq-1")]

    async def test_account_selection_after_escalation_is_capacity_aware(
        self, state, monkeypatch: pytest.MonkeyPatch
    ):
        self._pooled_env(monkeypatch, LLM_TOKENS_PER_MINUTE="10000")
        clients = self._install(monkeypatch)
        settings = get_settings()
        budget_for(LLMProvider.GROQ, LARGE, settings.llm_tokens_per_minute, "groq-1").record_now(
            10_000
        )

        await developer_agent({**state, "static_report": _static()})

        assert clients[(LARGE, "groq-1")].calls == 0
        assert clients[(LARGE, "groq-2")].calls == 1

    async def test_an_exhausted_account_does_not_itself_escalate(
        self, state, monkeypatch: pytest.MonkeyPatch
    ):
        """A full window is a resource fact, never evidence about the model."""
        self._pooled_env(monkeypatch, LLM_TOKENS_PER_MINUTE="10000")
        clients = self._install(monkeypatch)
        settings = get_settings()
        budget_for(LLMProvider.GROQ, SMALL, settings.llm_tokens_per_minute, "groq-1").record_now(
            10_000
        )

        update = await developer_agent({**state, "generated_services": []})

        assert update["service_escalations"] == {}
        assert clients[(SMALL, "groq-2")].calls == 1

    def test_the_ceiling_still_matches_the_reservation(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Escalation changes the model, never the figure the meter reserves."""
        self._pooled_env(monkeypatch, ADAPTIVE_CEILINGS="true")
        self._install(monkeypatch)
        from tests.test_structured import Widget

        with recording("run-1", Stage.DEVELOPER.value) as ledger:
            registry.get_structured_llm(
                Widget,
                Purpose.HEAVY,
                demand=1800,
                signal=RouteSignal(**{**TINY.__dict__, "escalations": 1}),
            ).invoke("go")

        entry = ledger.records[0]
        assert entry.tier == "medium"
        assert entry.ceiling_tokens == 2048
        assert entry.reserved_tokens == entry.estimated_tokens + 2048


class TestTheLedgerRecordsEscalation:
    """Read through the registry directly.

    `run_stage` binds a ledger of its own for the stage, so a `recording()` scope
    placed around `developer_agent` would sit outside it and stay empty. What is
    checked here is the registry-to-ledger half — that a plan carrying an
    escalation is written down — while the developer half, that it builds the
    right signal, is checked above by the tiers it asked for.
    """

    @staticmethod
    def _call(monkeypatch: pytest.MonkeyPatch, signal: RouteSignal):
        TestEscalationUsesTheExistingPool._install(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", SECRET)
        monkeypatch.setenv("ROUTING_ENABLED", "true")
        from core.config import reset_settings_cache

        reset_settings_cache()
        reset_budgets()

        from tests.test_structured import Widget

        with recording("run-1", Stage.DEVELOPER.value) as ledger:
            registry.get_structured_llm(Widget, Purpose.HEAVY, signal=signal).invoke("go")
        return ledger

    def test_it_records_both_tiers_and_the_reason(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._call(
            monkeypatch,
            RouteSignal(
                **{**TINY.__dict__, "escalations": 1, "escalation_reason": "failed to compile"}
            ),
        )

        entry = ledger.records[0]
        assert entry.difficulty == "low"
        assert entry.tier == "medium"
        assert entry.escalation_reason == "failed to compile"

    def test_an_unescalated_call_records_no_reason(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._call(monkeypatch, TINY)

        assert ledger.records[0].tier == "low"
        assert ledger.records[0].escalation_reason is None

    def test_the_escalated_call_is_charged_to_the_stronger_model(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        ledger = self._call(
            monkeypatch, RouteSignal(**{**TINY.__dict__, "escalations": 1})
        )

        assert ledger.records[0].model == LARGE

    def test_no_credential_reaches_the_record(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._call(
            monkeypatch,
            RouteSignal(**{**TINY.__dict__, "escalations": 1, "escalation_reason": "boom"}),
        )

        assert SECRET not in str([record.__dict__ for record in ledger.records])

    def test_the_report_still_breaks_down_by_tier(self):
        from llm.accounting import Ledger

        ledger = Ledger("r", Stage.DEVELOPER.value)
        ledger.record(
            purpose="heavy", provider="groq", model=LARGE, estimated_tokens=10,
            reserved_tokens=20, actual_tokens=None, duration_seconds=0.1,
            outcome="success", tier="medium", difficulty="low",
            escalation_reason="failed to compile",
        )

        assert set(ledger.report()["by_tier"]) == {"medium"}


class TestTheCapHoldsWhereverTheDecisionIsMade:
    """The configured maximum must mean what it says, for every caller.

    The developer agent already refuses to count past it. This checks the policy
    itself, because a limit that holds only by the convention of its one current
    caller is not a limit — and a hand-built signal is exactly how a future
    caller, or a stale checkpoint, could ask for more than was configured.
    """

    def test_a_signal_asking_for_more_than_the_maximum_is_clamped(self):
        over = RouteSignal(**{**TINY.__dict__, "escalations": 2})

        plan = plan_for(Purpose.HEAVY, over, routed())

        assert plan.escalations == 1
        assert plan.tier is ModelTier.MEDIUM  # not HIGH

    def test_a_far_larger_request_is_still_one_step(self):
        over = RouteSignal(**{**TINY.__dict__, "escalations": 99})

        assert plan_for(Purpose.HEAVY, over, routed()).tier is ModelTier.MEDIUM

    def test_raising_the_maximum_permits_the_second_step(self):
        over = RouteSignal(**{**TINY.__dict__, "escalations": 2})

        plan = plan_for(Purpose.HEAVY, over, routed(max_model_escalations=2))

        assert plan.escalations == 2
        assert plan.tier is ModelTier.HIGH

    def test_a_maximum_of_zero_disables_escalation_entirely(self):
        over = RouteSignal(**{**TINY.__dict__, "escalations": 1})

        plan = plan_for(Purpose.HEAVY, over, routed(max_model_escalations=0))

        assert plan.escalated is False
        assert plan.tier is ModelTier.LOW

    def test_a_negative_count_cannot_lower_a_tier(self):
        under = RouteSignal(**{**TYPICAL.__dict__, "escalations": -3})

        assert plan_for(Purpose.HEAVY, under, routed()).tier is ModelTier.MEDIUM
