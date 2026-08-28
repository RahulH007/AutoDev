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

    async def test_the_documents_are_produced(self, start_state, config, workspace, stub_llm):
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
