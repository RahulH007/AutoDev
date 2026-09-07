from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class Stage(StrEnum):
    """Pipeline nodes, in execution order."""

    PM = "pm_agent"
    ARCHITECTURE = "architecture_agent"
    DEVELOPER = "developer_agent"
    STATIC_GATE = "static_gate"
    QA = "qa_agent"
    TEST_RUNNER = "test_runner"

    @property
    def label(self) -> str:
        return {
            Stage.PM: "Product Manager",
            Stage.ARCHITECTURE: "Architect",
            Stage.DEVELOPER: "Developer",
            Stage.STATIC_GATE: "Static Gate",
            Stage.QA: "QA Engineer",
            Stage.TEST_RUNNER: "Test Runner",
        }[self]


class AgentStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# The two points where the graph interrupts for human review.
REVIEW_STAGES: tuple[Stage, ...] = (Stage.PM, Stage.ARCHITECTURE)

FEEDBACK_FIELD: dict[Stage, str] = {
    Stage.PM: "pm_feedback",
    Stage.ARCHITECTURE: "architect_feedback",
}


class MultiAgent(TypedDict, total=False):
    """State shared by every node in the graph."""

    # Identity and inputs
    run_id: str
    user_requirements: str

    # Agent outputs
    prd: dict[str, Any]
    architecture: dict[str, Any]
    # The architecture reduced to what a filesystem can check, derived by
    # core/contracts.py whenever the architecture agent writes a document — so a
    # revision replaces it and code is never judged against a superseded design.
    contract: dict[str, Any]
    code_manifest: dict[str, Any]
    # Which services the developer has written, by slug. The developer works one
    # service at a time, so a pass that fails partway has still produced
    # something; recording it lets the next pass skip what already exists.
    generated_services: list[str]
    failed_services: list[str]
    # How many times each service has been moved to a stronger model because
    # verification said the last one was not good enough, by slug. Separate from
    # `retry_count` on purpose: that counts whole developer passes, this counts
    # model upgrades within one service, and neither is derivable from the other.
    service_escalations: dict[str, int]
    # How many times each service has been sent to the model, by slug. Distinct
    # from `retry_count`, which counts developer *passes* over the whole project:
    # once regeneration became targeted the two stopped agreeing, because a pass
    # may rebuild one service and preserve three. Neither is derivable from the
    # other, and the global cap remains `retry_count`'s.
    service_attempts: dict[str, int]
    qa_report: dict[str, Any]

    # Verification outputs
    static_report: dict[str, Any]
    verification_report: dict[str, Any]
    # Which service each failure in those reports implicates, by slug, derived by
    # agents/attribution.py at the gates. Read-only: nothing routes, retries or
    # regenerates on it yet. Failures no service could be established for are kept
    # under agents.attribution.UNATTRIBUTED rather than assigned to one.
    service_failures: dict[str, list[str]]

    # Human-in-the-loop
    pm_feedback: str
    architect_feedback: str

    # Progress
    current_stage: str
    retry_count: int
    status: dict[str, str]
    error: str

    # What the run has spent on model calls, accumulated across stages, retries
    # and review pauses by agents/base.py:run_stage. Written by the accountant in
    # llm/accounting.py, never read by anything that makes a decision -- pacing
    # is the token budget's job. Read it with .get(): a run checkpointed before
    # this field existed resumes without it.
    cost_report: dict[str, Any]


def initial_state(run_id: str, user_requirements: str) -> MultiAgent:
    return {
        "run_id": run_id,
        "user_requirements": user_requirements,
        "prd": {},
        "architecture": {},
        "contract": {},
        "code_manifest": {},
        "generated_services": [],
        "failed_services": [],
        "service_escalations": {},
        "service_attempts": {},
        "qa_report": {},
        "static_report": {},
        "verification_report": {},
        "service_failures": {},
        "pm_feedback": "",
        "architect_feedback": "",
        "current_stage": "",
        "retry_count": 0,
        "status": {stage.value: AgentStatus.PENDING.value for stage in Stage},
        "cost_report": {},
    }
