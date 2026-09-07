"""End-to-end tests for the compiled pipeline.

The whole graph runs here with a stubbed LLM but a real filesystem and real
subprocesses, so the routing decisions are made against genuine compiler and
pytest output rather than canned reports.
"""

from __future__ import annotations

import pytest

from core.config import get_settings, reset_settings_cache
from core.paths import RunWorkspace
from graph.build_graph import (
    ARCHITECTURE,
    DEVELOPER,
    PM,
    QA,
    STATIC_GATE,
    TEST_RUNNER,
    architecture_review_router,
    build_workflow,
    developer_router,
    pm_review_router,
    qa_review_router,
    qa_router,
    static_gate_router,
)
from schema.architect_schema import ArchitectSchema
from schema.developer_schema import DeveloperSchema
from schema.qa_schema import QASchema
from state.state import AgentStatus, Stage, initial_state
from tests import fakes

REQUIREMENT = "Build an expense tracker with login and monthly reports."


@pytest.fixture
def config():
    return {"configurable": {"thread_id": "test-run"}, "recursion_limit": 60}


@pytest.fixture
def start_state(workspace: RunWorkspace):
    return initial_state(workspace.run_id, REQUIREMENT)


async def drive(workflow, state, config, *, approvals: int = 2):
    """Run to completion, approving each human review gate without feedback."""
    await workflow.ainvoke(state, config)
    for _ in range(approvals):
        snapshot = await workflow.aget_state(config)
        if not snapshot.next:
            break
        await workflow.ainvoke(None, config)
    return (await workflow.aget_state(config)).values


# ─────────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────────


class TestReviewRouters:
    def test_pm_feedback_loops_back(self):
        assert pm_review_router({"pm_feedback": "Add budget alerts"}) == PM

    def test_blank_pm_feedback_advances(self):
        assert pm_review_router({"pm_feedback": "   "}) == ARCHITECTURE
        assert pm_review_router({}) == ARCHITECTURE

    def test_architecture_feedback_loops_back(self):
        assert architecture_review_router({"architect_feedback": "Split the service"}) == ARCHITECTURE

    def test_blank_architecture_feedback_advances(self):
        assert architecture_review_router({}) == DEVELOPER


def _failed_at(stage: Stage) -> dict:
    return {"status": {stage.value: AgentStatus.FAILED.value}}


class TestFailureHalts:
    """A stage that produced nothing must not be handed to the next agent."""

    def test_a_failed_pm_ends_the_run(self):
        assert pm_review_router(_failed_at(Stage.PM)) == "END"

    def test_a_failed_architect_does_not_reach_the_developer(self):
        assert architecture_review_router(_failed_at(Stage.ARCHITECTURE)) == "END"

    def test_a_failed_developer_does_not_reach_the_gates(self):
        assert developer_router(_failed_at(Stage.DEVELOPER)) == "END"

    def test_a_failed_static_gate_ends_the_run(self):
        assert static_gate_router(_failed_at(Stage.STATIC_GATE)) == "END"

    def test_a_failed_qa_agent_does_not_reach_the_test_runner(self):
        assert qa_review_router(_failed_at(Stage.QA)) == "END"

    def test_a_failed_test_runner_ends_the_run(self):
        assert qa_router(_failed_at(Stage.TEST_RUNNER)) == "END"

    def test_failure_outranks_pending_feedback(self):
        state = _failed_at(Stage.ARCHITECTURE) | {"architect_feedback": "Split the service"}
        assert architecture_review_router(state) == "END"

    def test_a_healthy_stage_still_advances(self):
        healthy = {"status": {s.value: AgentStatus.COMPLETED.value for s in Stage}}
        assert developer_router(healthy) == STATIC_GATE
        assert qa_review_router(healthy) == TEST_RUNNER


class TestStaticGateRouter:
    def test_clean_code_reaches_qa(self):
        assert static_gate_router({"static_report": {"ran": True, "passed": True}}) == QA

    def test_a_gate_that_did_not_run_does_not_block(self):
        assert static_gate_router({"static_report": {"ran": False}}) == QA
        assert static_gate_router({}) == QA

    def test_broken_code_skips_the_qa_model_call(self):
        state = {"static_report": {"ran": True, "passed": False}, "retry_count": 1}
        assert static_gate_router(state) == DEVELOPER

    def test_the_retry_budget_is_respected(self):
        state = {"static_report": {"ran": True, "passed": False}, "retry_count": 3}
        assert static_gate_router(state) == "END"


class TestQaRouter:
    def test_a_clean_run_ends(self):
        state = {
            "retry_count": 1,
            "verification_report": {"ran": True, "passed": True, "services": []},
            "qa_report": {"critical_issues": 0, "service_reports": [{"code_quality_score": 9}]},
        }
        assert qa_router(state) == "END"

    def test_failing_tests_outrank_a_good_review_score(self):
        state = {
            "retry_count": 1,
            "verification_report": {
                "ran": True,
                "passed": False,
                "services": [{"service": "api", "failed": 2, "errors": 0}],
            },
            "qa_report": {"critical_issues": 0, "service_reports": [{"code_quality_score": 10}]},
        }
        assert qa_router(state) == DEVELOPER

    def test_a_service_that_could_not_run_its_tests_is_reworked(self):
        state = {
            "retry_count": 1,
            "verification_report": {
                "ran": False,
                "passed": True,
                "services": [{"service": "api", "error": "Dependency install failed."}],
            },
            "qa_report": {"critical_issues": 0, "service_reports": []},
        }
        assert qa_router(state) == DEVELOPER

    def test_critical_bugs_trigger_rework(self):
        state = {"retry_count": 1, "qa_report": {"critical_issues": 2, "service_reports": []}}
        assert qa_router(state) == DEVELOPER

    def test_a_low_score_triggers_rework(self):
        state = {
            "retry_count": 1,
            "qa_report": {
                "critical_issues": 0,
                "service_reports": [{"service_name": "api", "code_quality_score": 4}],
            },
        }
        assert qa_router(state) == DEVELOPER

    def test_a_missing_score_fails_closed(self):
        state = {
            "retry_count": 1,
            "qa_report": {"critical_issues": 0, "service_reports": [{"service_name": "api"}]},
        }
        assert qa_router(state) == DEVELOPER

    def test_the_retry_budget_stops_an_endless_loop(self):
        state = {"retry_count": 3, "qa_report": {"critical_issues": 99, "service_reports": []}}
        assert qa_router(state) == "END"

    def test_the_quality_threshold_is_configurable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MIN_QUALITY_SCORE", "3")
        reset_settings_cache()
        assert get_settings().min_quality_score == 3

        state = {
            "retry_count": 1,
            "verification_report": {},
            "qa_report": {
                "critical_issues": 0,
                "service_reports": [{"service_name": "api", "code_quality_score": 4}],
            },
        }
        assert qa_router(state) == "END"


# ─────────────────────────────────────────────────────────────────
# Human review gates
# ─────────────────────────────────────────────────────────────────


class TestReviewInterrupts:
    async def test_it_pauses_after_the_pm_agent(self, start_state, config, stub_llm):
        workflow = build_workflow()
        await workflow.ainvoke(start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == (ARCHITECTURE,)
        assert snapshot.values["prd"]["product_name"] == "SpendWise"
        assert not snapshot.values.get("architecture")

    async def test_it_pauses_again_after_the_architect(self, start_state, config, stub_llm):
        workflow = build_workflow()
        await workflow.ainvoke(start_state, config)
        await workflow.ainvoke(None, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == (DEVELOPER,)
        assert snapshot.values["architecture"]["architecture_style"]

    async def test_feedback_sends_the_prd_back_to_the_pm(self, start_state, config, stub_llm):
        workflow = build_workflow()
        await workflow.ainvoke(start_state, config)

        await workflow.aupdate_state(config, {"pm_feedback": "Add budget alerts"})
        await workflow.ainvoke(None, config)

        assert stub_llm.calls_for(type(fakes.build_prd())) == 2
        rendered = fakes.render_all(stub_llm.prompts)
        assert "Add budget alerts" in rendered

        # The loop must clear the feedback, or it would revise forever.
        snapshot = await workflow.aget_state(config)
        assert snapshot.values["pm_feedback"] == ""


# ─────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────


class TestHappyPath:
    async def test_a_clean_run_reaches_the_end(self, start_state, config, workspace, stub_llm):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == ()

        assert values["prd"]["product_name"] == "SpendWise"
        assert values["architecture"]["services"]
        assert values["code_manifest"]
        assert values["qa_report"]["passed"]

    async def test_every_stage_is_marked_complete(self, start_state, config, stub_llm):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        for stage in Stage:
            assert values["status"][stage.value] == AgentStatus.COMPLETED.value, stage

    async def test_the_code_lands_in_the_run_workspace(
        self, start_state, config, workspace, stub_llm
    ):
        workflow = build_workflow()
        await drive(workflow, start_state, config)

        service = workspace.service_source(fakes.SERVICE_NAME)
        assert (service / "app" / "calculator.py").is_file()
        assert (workspace.source / "requirements.txt").is_file()
        assert (workspace.source / "README.md").is_file()

    async def test_the_documents_are_produced(self, start_state, config, workspace, stub_llm, with_pdfs):
        workflow = build_workflow()
        await drive(workflow, start_state, config)

        for name in ("product_manager", "architecture", "developer", "qa"):
            assert (workspace.artifacts / f"{name}.json").is_file(), name
            assert (workspace.artifacts / f"{name}.pdf").is_file(), name

    async def test_the_static_gate_and_tests_really_ran(self, start_state, config, stub_llm):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["static_report"]["ran"]
        assert values["static_report"]["passed"]

        verification = values["verification_report"]
        assert verification["ran"]
        assert verification["passed"], verification
        assert sum(s["passed"] for s in verification["services"]) == 4

    async def test_a_clean_run_costs_one_developer_pass(self, start_state, config, stub_llm):
        workflow = build_workflow()
        await drive(workflow, start_state, config)

        assert stub_llm.calls_for(DeveloperSchema) == 1
        assert stub_llm.calls_for(QASchema) == 1


class TestStaticGateShortCircuit:
    async def test_broken_code_goes_back_to_the_developer_without_a_qa_pass(
        self, start_state, config, stub_llm
    ):
        """The point of the gate: a syntax error must not cost a review call."""
        stub_llm.set(
            DeveloperSchema,
            fakes.build_developer_output(broken=True),
            fakes.build_developer_output(),
        )

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert stub_llm.calls_for(DeveloperSchema) == 2
        # QA ran once, after the fix — never on the code that would not parse.
        assert stub_llm.calls_for(QASchema) == 1
        assert values["static_report"]["passed"]
        assert values["retry_count"] == 2

    async def test_the_fix_prompt_carries_the_compiler_error(self, start_state, config, stub_llm):
        stub_llm.set(
            DeveloperSchema,
            fakes.build_developer_output(broken=True),
            fakes.build_developer_output(),
        )

        workflow = build_workflow()
        await drive(workflow, start_state, config)

        rendered = fakes.render_all(stub_llm.prompts)
        assert "COMPILE AND LINT FAILURES" in rendered
        assert "app/calculator.py" in rendered

    async def test_code_that_never_compiles_stops_at_the_retry_budget(
        self, start_state, config, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MAX_DEVELOPER_RETRIES", "2")
        reset_settings_cache()

        stub_llm.set(DeveloperSchema, fakes.build_developer_output(broken=True))

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == ()
        assert stub_llm.calls_for(DeveloperSchema) == 2
        assert stub_llm.calls_for(QASchema) == 0
        assert not values["static_report"]["passed"]
        assert "does not compile" in values["error"]


class TestFailingTestsLoop:
    async def test_a_failing_test_sends_the_code_back(self, start_state, config, stub_llm):
        stub_llm.set(
            QASchema,
            fakes.build_qa_report(failing_tests=True),
            fakes.build_qa_report(),
        )

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert stub_llm.calls_for(DeveloperSchema) == 2
        assert values["verification_report"]["passed"]

    async def test_the_fix_prompt_carries_the_real_assertion(self, start_state, config, stub_llm):
        stub_llm.set(
            QASchema,
            fakes.build_qa_report(failing_tests=True),
            fakes.build_qa_report(),
        )

        workflow = build_workflow()
        await drive(workflow, start_state, config)

        rendered = fakes.render_all(stub_llm.prompts)
        assert "REAL TEST RESULTS" in rendered
        assert "test_add_is_wrong" in rendered

    async def test_critical_bugs_send_the_code_back(self, start_state, config, stub_llm):
        stub_llm.set(
            QASchema,
            fakes.build_qa_report(critical_issues=1),
            fakes.build_qa_report(),
        )

        workflow = build_workflow()
        await drive(workflow, start_state, config)

        assert stub_llm.calls_for(DeveloperSchema) == 2
        rendered = fakes.render_all(stub_llm.prompts)
        assert "REVIEWER-REPORTED BUGS" in rendered

    async def test_persistent_failures_stop_at_the_retry_budget(
        self, start_state, config, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MAX_DEVELOPER_RETRIES", "2")
        reset_settings_cache()

        stub_llm.set(QASchema, fakes.build_qa_report(critical_issues=1))

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == ()
        assert values["retry_count"] == 2
        assert stub_llm.calls_for(DeveloperSchema) == 2


class TestFailedStageEndsTheRun:
    async def test_a_failing_architect_never_reaches_the_developer(
        self, start_state, config, stub_llm
    ):
        """The bug this guards: the developer used to write code from an empty
        architecture, burying the architect's error under whatever broke next."""
        stub_llm.set(ArchitectSchema, RuntimeError("provider rejected the tool call"))

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == ()

        assert stub_llm.calls_for(DeveloperSchema) == 0
        assert stub_llm.calls_for(QASchema) == 0
        assert values["status"][Stage.ARCHITECTURE.value] == AgentStatus.FAILED.value
        assert values["status"][Stage.DEVELOPER.value] == AgentStatus.PENDING.value
        assert "provider rejected the tool call" in values["error"]


class TestStaleEvidence:
    async def test_a_new_developer_pass_clears_the_previous_reports(
        self, start_state, config, stub_llm
    ):
        """Otherwise the router would judge fresh code on last attempt's failures."""
        stub_llm.set(
            QASchema,
            fakes.build_qa_report(critical_issues=1),
            fakes.build_qa_report(),
        )

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["qa_report"]["critical_issues"] == 0
        assert values["verification_report"]["passed"]


class TestFailureAttribution:
    """The failure-to-service mapping, derived at the gates.

    Read-only in this phase: the mapping is computed and stored, and nothing
    routes, retries or regenerates on it. The tests below check both halves —
    that it is derived from real evidence, and that the graph behaves exactly as
    it did before it existed.
    """

    async def test_a_compile_failure_is_attributed_at_the_static_gate(
        self, start_state, config, stub_llm
    ):
        """Broken code really is compiled here, so the mapping is over real output."""
        stub_llm.set(DeveloperSchema, fakes.build_developer_output(broken=True))

        workflow = build_workflow()
        await drive(workflow, start_state, config)
        values = (await workflow.aget_state(config)).values

        assert fakes.SERVICE_SLUG in values["service_failures"]
        assert any(
            "calculator.py" in line for line in values["service_failures"][fakes.SERVICE_SLUG]
        )

    async def test_a_failing_test_is_attributed_at_the_test_runner(
        self, start_state, config, stub_llm
    ):
        stub_llm.set(QASchema, fakes.build_qa_report(failing_tests=True))
        stub_llm.set(DeveloperSchema, fakes.build_developer_output())

        workflow = build_workflow()
        await drive(workflow, start_state, config)
        values = (await workflow.aget_state(config)).values

        assert any(
            "test_add_is_wrong" in line
            for line in values["service_failures"].get(fakes.SERVICE_SLUG, [])
        )

    async def test_a_clean_run_attributes_nothing(self, start_state, config, stub_llm):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["service_failures"] == {}

    async def test_a_fresh_developer_pass_clears_the_previous_attribution(
        self, start_state, config, stub_llm
    ):
        """Same reason the reports themselves are cleared: nothing stale on screen."""
        stub_llm.set(
            DeveloperSchema,
            fakes.build_developer_output(broken=True),
            fakes.build_developer_output(),
        )

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["service_failures"] == {}

    async def test_the_attribution_agrees_with_the_report_it_came_from(
        self, start_state, config, stub_llm
    ):
        stub_llm.set(DeveloperSchema, fakes.build_developer_output(broken=True))

        workflow = build_workflow()
        await drive(workflow, start_state, config)
        values = (await workflow.aget_state(config)).values

        attributed = [
            line for lines in values["service_failures"].values() for line in lines
        ]
        assert sorted(attributed) == sorted(values["static_report"]["failures"])

    async def test_state_written_before_the_field_existed_still_runs(
        self, start_state, config, stub_llm
    ):
        """A run checkpointed by an earlier version has no `service_failures`."""
        older = dict(start_state)
        del older["service_failures"]

        workflow = build_workflow()
        values = await drive(workflow, older, config)

        assert values["status"][Stage.TEST_RUNNER.value] == AgentStatus.COMPLETED.value
        assert values["service_failures"] == {}


class TestAttributionDoesNotRoute:
    """Every router must reach the same decision with the mapping as without it.

    Surgical regeneration is Phase 2. Until then an attributed failure is an
    observation, and a router that started reading one would be changing the
    pipeline's behaviour ahead of the phase that is meant to.
    """

    STATE = {
        "static_report": {"ran": True, "passed": False},
        "retry_count": 1,
        "service_failures": {"backend-api": ["backend-api/app/main.py:1: invalid syntax"]},
    }

    def test_the_static_gate_router_ignores_it(self):
        without = {k: v for k, v in self.STATE.items() if k != "service_failures"}

        assert static_gate_router(self.STATE) == static_gate_router(without) == DEVELOPER

    def test_the_static_gate_router_still_honours_the_retry_budget(self):
        state = {**self.STATE, "retry_count": 3}

        assert static_gate_router(state) == "END"

    def test_the_qa_router_ignores_it(self):
        state = {
            "retry_count": 1,
            "verification_report": {"ran": True, "passed": True, "services": []},
            "qa_report": {"critical_issues": 0, "service_reports": [{"code_quality_score": 9}]},
        }
        attributed = {**state, "service_failures": {"backend-api": ["something"]}}

        assert qa_router(attributed) == qa_router(state) == "END"

    def test_the_developer_router_ignores_it(self):
        assert developer_router(self.STATE) == developer_router({}) == STATIC_GATE

    def test_attribution_cannot_conjure_rework_out_of_a_clean_report(self):
        """The mapping says a service is implicated; only the reports may route."""
        state = {
            "retry_count": 1,
            "verification_report": {"ran": True, "passed": True, "services": []},
            "qa_report": {"critical_issues": 0, "service_reports": [{"code_quality_score": 9}]},
            "service_failures": {"backend-api": ["a failure from a previous pass"]},
        }

        assert qa_router(state) == "END"

    async def test_the_retry_count_is_unchanged_by_attribution(
        self, start_state, config, stub_llm
    ):
        """Same fixture as TestStaticGateShortCircuit, same count."""
        stub_llm.set(
            DeveloperSchema,
            fakes.build_developer_output(broken=True),
            fakes.build_developer_output(),
        )

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["retry_count"] == 2
        assert stub_llm.calls_for(DeveloperSchema) == 2
        assert stub_llm.calls_for(QASchema) == 1


class TestArchitectureContract:
    """The architecture a human approved, checked against what was delivered.

    Derived at the architecture stage and validated inside the static gate, so a
    design mismatch reaches the developer through the machinery that already
    carries a syntax error — no extra node, no extra router, no extra retry
    budget.
    """

    async def test_the_contract_is_stored_in_state(self, start_state, config, stub_llm):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert [s["slug"] for s in values["contract"]["services"]] == [fakes.SERVICE_SLUG]

    async def test_a_run_that_meets_its_contract_passes_the_gate(
        self, start_state, config, stub_llm
    ):
        """The fake project really does write the files its architecture names."""
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["static_report"]["passed"]
        assert not any("the architecture" in f for f in values["static_report"]["failures"])

    async def test_a_missing_required_file_is_reported_by_the_static_gate(
        self, start_state, config, stub_llm
    ):
        """The architecture asks for two files; the developer writes only one."""
        stub_llm.set(ArchitectSchema, fakes.build_architecture())
        stub_llm.set(DeveloperSchema, _only_calculator())

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert not values["static_report"]["passed"]
        assert any("app/store.py" in f for f in values["static_report"]["failures"])

    async def test_a_contract_violation_sends_the_code_back_to_the_developer(
        self, start_state, config, stub_llm
    ):
        """Ordinary rework, through the existing static gate router."""
        stub_llm.set(DeveloperSchema, _only_calculator(), fakes.build_developer_output())

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert stub_llm.calls_for(DeveloperSchema) == 2
        assert values["static_report"]["passed"]

    async def test_a_contract_violation_is_attributed_to_its_service(
        self, start_state, config, stub_llm
    ):
        stub_llm.set(DeveloperSchema, _only_calculator())

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        failures = values["service_failures"][fakes.SERVICE_SLUG]
        assert any("app/store.py" in line for line in failures)

    async def test_state_written_before_the_field_existed_still_runs(
        self, start_state, config, stub_llm
    ):
        """A run checkpointed by an earlier version has no `contract` at all."""
        older = dict(start_state)
        del older["contract"]

        workflow = build_workflow()
        values = await drive(workflow, older, config)

        assert values["status"][Stage.TEST_RUNNER.value] == AgentStatus.COMPLETED.value
        # The architecture stage derives one on the way past, so the run ends with it.
        assert values["contract"]["services"]

    async def test_a_revised_architecture_is_what_the_code_is_judged_against(
        self, start_state, config, stub_llm
    ):
        """The old contract must not survive the revision that replaced it."""
        revised = fakes.build_architecture()
        revised.project_structure[0].key_files = ["app/calculator.py"]
        stub_llm.set(ArchitectSchema, fakes.build_architecture(), revised)

        workflow = build_workflow()
        await workflow.ainvoke(start_state, config)          # pauses on the PRD
        await workflow.ainvoke(None, config)                  # pauses on architecture v1
        await workflow.aupdate_state(config, {"architect_feedback": "Drop the store."})
        await workflow.ainvoke(None, config)                  # pauses on architecture v2
        await workflow.ainvoke(None, config)                  # approved; runs on

        values = (await workflow.aget_state(config)).values
        assert values["contract"]["services"][0]["key_files"] == ["app/calculator.py"]


class TestContractDoesNotChangeRouting:
    async def test_the_retry_count_is_unchanged_by_a_contract_pass(
        self, start_state, config, stub_llm
    ):
        """A run that meets its contract counts exactly one developer attempt."""
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["retry_count"] == 1

    async def test_the_model_call_count_is_unchanged(self, start_state, config, stub_llm):
        """No contract call: derivation is a pure function of the architecture."""
        workflow = build_workflow()
        await drive(workflow, start_state, config)

        assert stub_llm.calls_for(ArchitectSchema) == 1
        assert stub_llm.calls_for(DeveloperSchema) == 1
        assert stub_llm.calls_for(QASchema) == 1

    async def test_a_contract_violation_still_honours_the_retry_budget(
        self, start_state, config, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MAX_DEVELOPER_RETRIES", "2")
        reset_settings_cache()
        stub_llm.set(DeveloperSchema, _only_calculator())

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == ()
        assert stub_llm.calls_for(DeveloperSchema) == 2
        assert stub_llm.calls_for(QASchema) == 0
        assert values["retry_count"] == 2


def _only_calculator():
    """Developer output that omits `app/store.py`, which the architecture requires."""
    output = fakes.build_developer_output()
    output.services[0].files = [
        file for file in output.services[0].files if "store" not in file.file_path
    ]
    return output


# ─────────────────────────────────────────────────────────────────
# Surgical repair, end to end
# ─────────────────────────────────────────────────────────────────

REPORTS = "Reports"
REPORTS_SLUG = "reports"

REPORTS_SOURCE = '''"""Monthly report totals."""


def total(amounts: list[float]) -> float:
    return sum(amounts)
'''


def _two_service_architecture():
    """The fake architecture plus a second, entirely independent service."""
    from schema.architect_schema import ProjectStructure, Service

    architecture = fakes.build_architecture()
    architecture.services.append(
        Service(
            name=REPORTS,
            description="Summarises spending by category.",
            tech_stack=["Python"],
            dependencies=[],
        )
    )
    architecture.project_structure.append(
        ProjectStructure(
            service_name=REPORTS, folders=["app"], key_files=["app/report.py"]
        )
    )
    return architecture


def _reports_code():
    from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode

    return DeveloperSchema(
        project_name="SpendWise",
        services=[
            ServiceCode(
                service_name=REPORTS,
                files=[
                    CodeFile(
                        file_path="app/report.py",
                        file_name="report.py",
                        language="python",
                        code=REPORTS_SOURCE,
                        description="Report totals.",
                    )
                ],
            )
        ],
    )


class TestSurgicalRepairEndToEnd:
    """One service fails; only that service is rebuilt, and the run still completes.

    The whole pipeline runs here — real compiler, real pytest, real routers —
    with only the model stubbed. What is being established is that the pieces
    built across every phase compose: attribution places the failure, evidence is
    narrowed to it, targeting rebuilds it alone, the contract keeps the run
    honest about the service that is still outstanding, and the graph reaches the
    same completed state a clean run does.
    """

    @pytest.fixture
    def broken_backend(self, stub_llm):
        """Backend generated broken, Reports healthy, then a fixed Backend."""
        stub_llm.set(ArchitectSchema, _two_service_architecture())
        stub_llm.set(
            DeveloperSchema,
            fakes.build_developer_output(broken=True),  # pass 1: Backend API
            _reports_code(),                            # pass 1: Reports
            fakes.build_developer_output(),             # pass 2: Backend API only
        )
        return stub_llm

    async def test_the_run_completes(self, start_state, config, broken_backend):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        snapshot = await workflow.aget_state(config)
        assert snapshot.next == ()
        assert not values.get("error")
        for stage in Stage:
            assert values["status"][stage.value] == AgentStatus.COMPLETED.value, stage

    async def test_only_the_failing_service_was_rebuilt(
        self, start_state, config, broken_backend
    ):
        """Three developer calls, not four: two to build, one to repair."""
        workflow = build_workflow()
        await drive(workflow, start_state, config)

        assert broken_backend.calls_for(DeveloperSchema) == 3

    async def test_the_healthy_service_is_byte_identical(
        self, start_state, config, workspace, broken_backend
    ):
        workflow = build_workflow()
        await drive(workflow, start_state, config)

        report = workspace.service_source(REPORTS) / "app" / "report.py"
        assert report.is_file()
        assert report.read_text(encoding="utf-8") == REPORTS_SOURCE

    async def test_the_repaired_service_passes_verification(
        self, start_state, config, broken_backend
    ):
        """The real compiler and the real pytest, not a canned report."""
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["static_report"]["ran"]
        assert values["static_report"]["passed"]
        assert values["verification_report"]["ran"]
        assert values["verification_report"]["passed"], values["verification_report"]

    async def test_the_artifact_still_describes_the_whole_project(
        self, start_state, config, workspace, broken_backend
    ):
        from utils.json_utils import load_json

        workflow = build_workflow()
        await drive(workflow, start_state, config)

        artifact = load_json(workspace.artifacts / "developer.json")
        names = [service["service_name"] for service in artifact["services"]]
        assert sorted(names) == sorted([fakes.SERVICE_NAME, REPORTS])
        assert len(names) == len(set(names))

    async def test_the_manifest_holds_both_services(
        self, start_state, config, broken_backend
    ):
        from core import manifest as manifest_util

        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert manifest_util.services(values["code_manifest"]) == sorted(
            [fakes.SERVICE_SLUG, REPORTS_SLUG]
        )

    async def test_the_counters_tell_the_two_stories_apart(
        self, start_state, config, broken_backend
    ):
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["retry_count"] == 2  # two developer passes over the project
        assert values["service_attempts"][fakes.SERVICE_SLUG] == 2  # built twice
        assert values["service_attempts"][REPORTS_SLUG] == 1  # built once, preserved after
        assert values["failed_services"] == []

    async def test_nothing_was_escalated(self, start_state, config, broken_backend):
        """Routing is off by default, so a repair costs no model upgrade."""
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert values["service_escalations"] == {}

    async def test_the_ledger_shows_the_smaller_second_pass(
        self, start_state, config, broken_backend
    ):
        """The stub bypasses the meter, so the shape is what is checked."""
        workflow = build_workflow()
        values = await drive(workflow, start_state, config)

        assert set(values["cost_report"]) >= {"calls", "reserved_tokens", "by_stage"}
