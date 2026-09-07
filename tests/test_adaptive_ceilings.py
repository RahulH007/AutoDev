"""Sizing a completion to the answer actually being asked for.

A per-purpose ceiling has to be set for the largest answer that purpose ever
gives, and every smaller call then reserves that same figure against the
per-minute window. A three-file service and a thirty-file service are both HEAVY,
and both hold 4,096 tokens of the minute whether they need them or not.

Adaptive ceilings narrow that figure when — and only when — something
deterministic is known beforehand about the size of the answer. The configured
ceiling stays the hard maximum, a floor stops it shrinking into truncation, and a
caller with nothing to say gets exactly what it got before.

The dangerous direction is downward: a ceiling set too low truncates a structured
response mid-JSON, fails every rung of the ladder, and costs more than the window
it saved. So most of what follows is about the guards rather than the saving.
"""

from __future__ import annotations

from typing import Any

import pytest

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
from state.state import Stage
from tests.test_config_and_registry import MeteredFakeModel

CONFIGURED = {
    Purpose.HEAVY: 4096,
    Purpose.STRUCTURED: 3500,
    Purpose.TEXT: 2000,
    Purpose.CHEAP: 1000,
}


def adaptive(**overrides: Any) -> Settings:
    """Settings with adaptive sizing on and everything else at its default."""
    return Settings(google_api_key="k", adaptive_ceilings=True, **overrides)


@pytest.fixture
def adaptive_env(monkeypatch: pytest.MonkeyPatch):
    """Turn adaptive sizing on for the process-wide settings a run would use."""
    monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
    reset_settings_cache()
    registry.reset_cache()
    reset_budgets()
    yield
    reset_settings_cache()
    registry.reset_cache()


def _budget(purpose: Purpose):
    settings = get_settings()
    model = registry.model_name_for(LLMProvider.GOOGLE, purpose, settings)
    return budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)


# ── Off by default ───────────────────────────────────────────────


class TestDisabledIsTodaysBehaviour:
    """The flag defaults to off, and off must mean nothing whatsoever changed."""

    def test_the_flag_is_off_by_default(self):
        assert get_settings().adaptive_ceilings is False

    def test_the_configured_ceilings_are_exactly_as_before(self):
        settings = get_settings()

        for purpose, expected in CONFIGURED.items():
            assert settings.max_output_for(purpose) == expected

    def test_a_demand_is_ignored_while_the_flag_is_off(self):
        settings = Settings(google_api_key="k")

        for purpose, expected in CONFIGURED.items():
            assert settings.max_output_for(purpose, 100) == expected

    def test_even_an_absurd_demand_is_ignored(self):
        settings = Settings(google_api_key="k")

        assert settings.max_output_for(Purpose.HEAVY, 1) == 4096
        assert settings.max_output_for(Purpose.HEAVY, 999_999) == 4096

    def test_a_client_is_built_with_the_configured_figure(self):
        registry.reset_cache()
        model = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GOOGLE, Settings(google_api_key="k")
        )

        assert model.max_output_tokens == 4096


# ── The algorithm ────────────────────────────────────────────────


class TestNarrowing:
    def test_a_small_demand_shrinks_the_ceiling(self):
        assert adaptive().max_output_for(Purpose.HEAVY, 1800) == 2048

    def test_the_demand_is_rounded_up_to_a_whole_block(self):
        """Slack, and few enough distinct values for the model cache to reuse."""
        settings = adaptive()

        assert settings.max_output_for(Purpose.HEAVY, 1793) == 2048
        assert settings.max_output_for(Purpose.HEAVY, 2048) == 2048
        assert settings.max_output_for(Purpose.HEAVY, 2049) == 2304

    def test_the_block_size_is_configurable(self):
        assert adaptive(adaptive_ceiling_block=1000).max_output_for(Purpose.HEAVY, 1800) == 2000

    def test_no_demand_still_means_the_configured_ceiling(self):
        """A caller that knows nothing must say nothing, not guess."""
        settings = adaptive()

        for purpose, expected in CONFIGURED.items():
            assert settings.max_output_for(purpose) == expected
            assert settings.max_output_for(purpose, None) == expected

    def test_a_zero_or_negative_demand_is_treated_as_no_demand(self):
        settings = adaptive()

        assert settings.max_output_for(Purpose.HEAVY, 0) == 4096
        assert settings.max_output_for(Purpose.HEAVY, -5) == 4096

    def test_it_is_deterministic(self):
        settings = adaptive()

        assert settings.max_output_for(Purpose.HEAVY, 1800) == settings.max_output_for(
            Purpose.HEAVY, 1800
        )


class TestTheConfiguredCeilingIsTheMaximum:
    def test_a_large_demand_cannot_raise_the_ceiling(self):
        assert adaptive().max_output_for(Purpose.HEAVY, 99_999) == 4096

    def test_that_holds_for_every_purpose(self):
        settings = adaptive()

        for purpose, expected in CONFIGURED.items():
            assert settings.max_output_for(purpose, 50_000) == expected

    def test_a_demand_just_over_the_ceiling_is_clamped_not_rounded_past_it(self):
        assert adaptive().max_output_for(Purpose.HEAVY, 4097) == 4096

    def test_a_purpose_opted_out_of_ceilings_is_left_alone(self):
        """Zero means "the provider decides"; there is no figure to narrow."""
        settings = adaptive(max_output_heavy=0)

        assert settings.max_output_for(Purpose.HEAVY, 500) == 0


class TestTheMinimumIsTheFloor:
    def test_a_tiny_demand_cannot_shrink_below_the_minimum(self):
        assert adaptive().max_output_for(Purpose.HEAVY, 10) == 1024

    def test_the_minimum_is_configurable(self):
        assert adaptive(adaptive_min_output_tokens=2048).max_output_for(Purpose.HEAVY, 10) == 2048

    def test_the_minimum_can_never_raise_a_ceiling_above_its_configured_value(self):
        """CHEAP is configured at 1,000, below the 1,024 floor. It stays at 1,000."""
        settings = adaptive()

        assert settings.max_output_for(Purpose.CHEAP, 10) == 1000

    def test_the_default_floor_is_not_below_the_smallest_shipped_ceiling(self):
        """The floor is anchored to a figure the project already runs on."""
        settings = get_settings()

        assert settings.adaptive_min_output_tokens >= settings.max_output_for(Purpose.CHEAP)


class TestMissingConfigurationIsSafe:
    def test_a_settings_object_built_without_the_new_fields_has_defaults(self):
        settings = Settings(google_api_key="k")

        assert settings.adaptive_ceilings is False
        assert settings.adaptive_min_output_tokens == 1024
        assert settings.adaptive_ceiling_block == 256

    def test_the_new_fields_are_settable_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        monkeypatch.setenv("ADAPTIVE_MIN_OUTPUT_TOKENS", "1500")
        monkeypatch.setenv("ADAPTIVE_CEILING_BLOCK", "500")
        reset_settings_cache()

        settings = get_settings()
        assert settings.adaptive_ceilings is True
        assert settings.max_output_for(Purpose.HEAVY, 100) == 1500
        assert settings.max_output_for(Purpose.HEAVY, 1600) == 2000

    def test_resetting_the_cache_picks_the_change_up_and_puts_it_back(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        assert get_settings().adaptive_ceilings is False

        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        reset_settings_cache()
        assert get_settings().adaptive_ceilings is True

        monkeypatch.delenv("ADAPTIVE_CEILINGS")
        reset_settings_cache()
        assert get_settings().adaptive_ceilings is False


# ── One figure, two consumers ────────────────────────────────────


class TestTheClientAndTheBudgetAgree:
    """The invariant the whole design hangs on.

    A client built for 4,096 whose call reserves 2,048 is a request the budget
    waves through and the provider answers with a 413.
    """

    def test_the_client_is_built_with_the_adapted_figure(self):
        registry.reset_cache()
        settings = adaptive()
        ceiling = settings.max_output_for(Purpose.HEAVY, 1800)

        model = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings, ceiling)

        assert ceiling == 2048
        assert model.max_output_tokens == 2048

    def test_the_reservation_matches_what_the_client_was_built_with(
        self, adaptive_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        reset_settings_cache()
        reset_budgets()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with recording("run-1", Stage.PM.value) as ledger:
            registry.get_text_llm(Purpose.TEXT, demand=900).invoke("hello there")

        entry = ledger.records[0]
        assert entry.ceiling_tokens == 1024
        assert entry.reserved_tokens == entry.estimated_tokens + 1024

    def test_a_client_asked_for_without_a_ceiling_resolves_the_configured_one(self):
        """Every caller outside the registry still gets today's behaviour."""
        registry.reset_cache()

        model = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, adaptive())

        assert model.max_output_tokens == 4096


class TestTheCacheKeepsCeilingsApart:
    def test_two_demands_on_one_model_are_two_clients(self):
        registry.reset_cache()
        settings = adaptive()

        small = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GOOGLE, settings,
            settings.max_output_for(Purpose.HEAVY, 1200),
        )
        large = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GOOGLE, settings,
            settings.max_output_for(Purpose.HEAVY, 3800),
        )

        assert small is not large
        assert small.max_output_tokens == 1280
        assert large.max_output_tokens == 3840

    def test_two_demands_that_round_to_one_block_share_a_client(self):
        """What the block rounding is for: a reusable cache."""
        registry.reset_cache()
        settings = adaptive()

        first = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GOOGLE, settings,
            settings.max_output_for(Purpose.HEAVY, 1800),
        )
        second = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GOOGLE, settings,
            settings.max_output_for(Purpose.HEAVY, 1900),
        )

        assert first is second

    def test_an_adapted_client_is_never_handed_to_an_unadapted_call(self):
        registry.reset_cache()
        settings = adaptive()

        adapted = registry.get_chat_model(
            Purpose.HEAVY, LLMProvider.GOOGLE, settings,
            settings.max_output_for(Purpose.HEAVY, 1200),
        )
        plain = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings)

        assert adapted is not plain
        assert plain.max_output_tokens == 4096


# ── The reservation, and the ledger that sees it ─────────────────


class TestTheReservationShrinks:
    """The demonstration the whole phase is for: same prompt, smaller hold."""

    @staticmethod
    def _reserve(monkeypatch: pytest.MonkeyPatch, *, on: bool, demand: int | None) -> int:
        reset_budgets()
        registry.reset_cache()
        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true" if on else "false")
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.get_text_llm(Purpose.TEXT, demand=demand).invoke("hello there")
        return _budget(Purpose.TEXT).used()

    def test_adaptive_off_holds_the_configured_ceiling(self, monkeypatch: pytest.MonkeyPatch):
        assert self._reserve(monkeypatch, on=False, demand=900) >= 2000

    def test_adaptive_on_holds_less_for_the_same_prompt(self, monkeypatch: pytest.MonkeyPatch):
        larger = self._reserve(monkeypatch, on=False, demand=900)
        smaller = self._reserve(monkeypatch, on=True, demand=900)

        assert smaller < larger
        assert smaller >= 1024

    def test_a_smaller_window_now_fits_a_call_it_would_have_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The saving, stated as the thing it buys."""
        from llm.budget import BudgetExceededError

        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "1500")
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        monkeypatch.setenv("ADAPTIVE_CEILINGS", "false")
        reset_settings_cache()
        reset_budgets()
        registry.reset_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(BudgetExceededError):
            registry.get_text_llm(Purpose.TEXT, demand=900).invoke("hello there")

        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        reset_settings_cache()
        reset_budgets()
        registry.reset_cache()

        registry.get_text_llm(Purpose.TEXT, demand=900).invoke("hello there")
        assert model.calls == 1


class TestTheLedgerSeesIt:
    @staticmethod
    def _record(monkeypatch: pytest.MonkeyPatch, *, on: bool):
        reset_budgets()
        registry.reset_cache()
        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true" if on else "false")
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "0")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with recording("run-1", Stage.DEVELOPER.value) as ledger:
            registry.get_text_llm(Purpose.TEXT, demand=900).invoke("hello there")
        return ledger.records[0]

    def test_the_record_carries_the_resolved_ceiling(self, monkeypatch: pytest.MonkeyPatch):
        assert self._record(monkeypatch, on=True).ceiling_tokens == 1024
        assert self._record(monkeypatch, on=False).ceiling_tokens == 2000

    def test_the_recorded_reservation_falls_with_it(self, monkeypatch: pytest.MonkeyPatch):
        adapted = self._record(monkeypatch, on=True)
        plain = self._record(monkeypatch, on=False)

        assert adapted.reserved_tokens < plain.reserved_tokens
        assert adapted.estimated_tokens == plain.estimated_tokens

    def test_no_second_counter_appeared(self, monkeypatch: pytest.MonkeyPatch):
        """The reservation is still the budget's own figure, copied."""
        record = self._record(monkeypatch, on=True)

        assert record.reserved_tokens == _budget(Purpose.TEXT).used()


# ── Nothing else moved ───────────────────────────────────────────


class TestStructuredCallsStaySafe:
    def test_a_structured_ceiling_is_never_shrunk_below_the_floor(self):
        settings = adaptive()

        for demand in (1, 50, 500, 1000):
            assert settings.max_output_for(Purpose.STRUCTURED, demand) >= 1024

    def test_the_ladder_is_untouched(self, monkeypatch: pytest.MonkeyPatch):
        from tests.test_accounting import StructuredModel
        from tests.test_structured import Widget

        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        reset_settings_cache()
        registry.reset_cache()
        model = StructuredModel(failing=("default",))
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        result = registry.get_structured_llm(Widget, Purpose.STRUCTURED, demand=1200).invoke("go")

        assert isinstance(result, Widget)
        assert model.calls[:2] == ["default", "json_schema"]

    def test_a_truncated_response_still_follows_the_existing_failure_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A ceiling too small is an ordinary provider failure, classified as before."""
        from llm.accounting import Outcome
        from tests.test_accounting import FlakyModel

        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        reset_settings_cache()
        registry.reset_cache()
        reset_budgets()
        model = FlakyModel(RuntimeError("Error code: 400 - response was truncated"))
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with recording("run-1", Stage.PM.value) as ledger, pytest.raises(Exception, match="400"):
            registry.get_text_llm(Purpose.TEXT, demand=900).invoke("hello there")

        assert ledger.records[0].outcome == Outcome.DEGRADE.value


class TestResolutionIsUnchanged:
    def test_model_and_provider_selection_do_not_move(self):
        settings = adaptive()

        assert registry.model_name_for(
            LLMProvider.GOOGLE, Purpose.HEAVY, settings
        ) == registry.model_name_for(
            LLMProvider.GOOGLE, Purpose.HEAVY, Settings(google_api_key="k")
        )

    def test_usable_providers_are_unaffected(self):
        assert registry.usable_providers(adaptive()) == registry.usable_providers(
            Settings(google_api_key="k")
        )

    def test_the_retrier_still_stops_at_a_non_transient_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from tests.test_accounting import FlakyModel

        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        reset_settings_cache()
        registry.reset_cache()
        model = FlakyModel(*[RuntimeError("Error code: 401 - bad key")] * 5)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(Exception, match="401"):
            registry.get_text_llm(Purpose.TEXT, demand=900).invoke("hello there")

        assert model.calls == 1

    def test_the_budget_is_still_keyed_by_provider_and_model_alone(self):
        """Not by ceiling: the quota belongs to the account, not to the call."""
        settings = adaptive()
        model = registry.model_name_for(LLMProvider.GOOGLE, Purpose.HEAVY, settings)

        first = budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)
        second = budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)

        assert first is second


# ── The developer stage ──────────────────────────────────────────


class TestPerServiceSizing:
    """Each service is sized from what the architect asked *it* for.

    The demand comes off the Phase 3 contract, which already joins a service to
    its required files and declarations — so no new plumbing, and nothing is
    guessed from the prompt text.
    """

    @staticmethod
    def _architecture(*specs: tuple[str, int]) -> dict[str, Any]:
        """Services named with a given number of required files each."""
        return {
            "architecture_style": "modular_monolith",
            "services": [
                {"name": name, "description": f"{name}.", "tech_stack": ["Python"]}
                for name, _ in specs
            ],
            "project_structure": [
                {
                    "service_name": name,
                    "folders": ["app"],
                    "key_files": [f"app/mod{i}.py" for i in range(count)],
                }
                for name, count in specs
            ],
        }

    @pytest.fixture
    def capture(self, monkeypatch: pytest.MonkeyPatch, workspace):
        """Drive the developer stage, recording the demand for each service."""
        from agents.developer_agent import developer_agent
        from core.contracts import derive_contract
        from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode
        from state.state import AgentStatus

        def code(name: str) -> DeveloperSchema:
            return DeveloperSchema(
                project_name="P",
                services=[
                    ServiceCode(
                        service_name=name,
                        files=[
                            CodeFile(
                                file_path="app/mod0.py",
                                file_name="mod0.py",
                                language="python",
                                code="x = 1\n",
                                description="A module.",
                            )
                        ],
                    )
                ],
            )

        async def _run(architecture: dict[str, Any], *, with_contract: bool = True) -> list[Any]:
            demands: list[Any] = []
            order: list[str] = []

            class Model:
                async def ainvoke(self, prompt: Any, config: Any = None) -> Any:
                    name = architecture["services"][len(order)]["name"]
                    order.append(name)
                    return code(name)

            # `**_` absorbs the registry's other optional arguments — the routing
            # signal, and whatever follows. Only the demand matters here.
            def fake(schema: Any, purpose: Any = None, settings: Any = None, demand=None, **_):
                demands.append(demand)
                return Model()

            monkeypatch.setattr(registry, "get_structured_llm", fake)

            state = {
                "run_id": workspace.run_id,
                "user_requirements": "Build it.",
                "prd": {},
                "architecture": architecture,
                "contract": derive_contract(architecture).model_dump(mode="json")
                if with_contract
                else {},
                "code_manifest": {},
                "status": {stage.value: AgentStatus.PENDING.value for stage in Stage},
                "retry_count": 0,
            }
            update = await developer_agent(state)
            return demands, order, update

        return _run

    async def test_a_bigger_service_asks_for_a_bigger_ceiling(self, capture):
        demands, _, _ = await capture(self._architecture(("Small", 2), ("Large", 12)))

        assert demands[0] < demands[1]

    async def test_the_demand_is_read_off_the_contract(self, capture):
        demands, _, _ = await capture(self._architecture(("Small", 2)))

        settings = get_settings()
        assert demands[0] == (
            settings.adaptive_service_base_tokens + settings.adaptive_service_file_tokens * 2
        )

    async def test_a_service_the_architecture_says_nothing_about_asks_for_nothing(
        self, capture
    ):
        """No measurable signal means the configured ceiling, not a guess."""
        demands, _, _ = await capture(self._architecture(("Vague", 0)))

        assert demands == [None]

    async def test_no_contract_means_no_demand(self, capture):
        demands, _, _ = await capture(
            self._architecture(("Small", 2)), with_contract=False
        )

        assert demands == [None]

    async def test_a_contract_that_cannot_be_read_is_not_a_failure(
        self, capture, monkeypatch: pytest.MonkeyPatch
    ):
        from agents.developer_agent import _parse_contract

        assert _parse_contract({"services": "not a list"}) is None
        assert _parse_contract(None) is None

    async def test_the_service_order_is_unchanged(self, capture):
        _, order, _ = await capture(self._architecture(("Small", 2), ("Large", 12)))

        assert order == ["Small", "Large"]

    async def test_the_call_count_is_still_one_per_service(self, capture):
        demands, order, update = await capture(
            self._architecture(("Small", 2), ("Large", 12))
        )

        assert len(demands) == len(order) == 2
        assert update["generated_services"] == ["small", "large"]

    async def test_the_demand_is_the_same_whether_adaptive_is_on_or_off(
        self, capture, adaptive_env
    ):
        """The flag decides what the resolver does with it, not what is measured."""
        demands, _, _ = await capture(self._architecture(("Small", 2)))

        settings = get_settings()
        assert demands[0] == (
            settings.adaptive_service_base_tokens + settings.adaptive_service_file_tokens * 2
        )
        assert settings.max_output_for(Purpose.HEAVY, demands[0]) < 4096


class TestContractsAndTheStaticGateAreUnaffected:
    def test_deriving_a_contract_does_not_depend_on_the_flag(self, workspace):
        from core.contracts import derive_contract
        from tests import fakes

        architecture = fakes.build_architecture().model_dump(mode="json")
        before = derive_contract(architecture).model_dump(mode="json")

        reset_settings_cache()
        after = derive_contract(architecture).model_dump(mode="json")

        assert before == after

    def test_the_static_gate_makes_no_model_call_to_be_sized(self, workspace):
        """It is deterministic; there is no ceiling to resolve for it at all."""
        from core.contracts import derive_contract
        from tests import fakes
        from verification.static_gate import run_static_gate

        workspace.write_source_file("Backend API", "app/calculator.py", "x = 1\n")
        workspace.write_source_file("Backend API", "app/store.py", "y = 2\n")
        contract = derive_contract(fakes.build_architecture().model_dump(mode="json"))

        report = run_static_gate(workspace, None, None, contract.model_dump())

        assert report.passed
