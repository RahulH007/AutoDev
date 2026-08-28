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
    code_manifest: dict[str, Any]
    qa_report: dict[str, Any]

    # Verification outputs
    static_report: dict[str, Any]
    verification_report: dict[str, Any]

    # Human-in-the-loop
    pm_feedback: str
    architect_feedback: str

    # Progress
    current_stage: str
    retry_count: int
    status: dict[str, str]
    error: str


def initial_state(run_id: str, user_requirements: str) -> MultiAgent:
    return {
        "run_id": run_id,
        "user_requirements": user_requirements,
        "prd": {},
        "architecture": {},
        "code_manifest": {},
        "qa_report": {},
        "static_report": {},
        "verification_report": {},
        "pm_feedback": "",
        "architect_feedback": "",
        "current_stage": "",
        "retry_count": 0,
        "status": {stage.value: AgentStatus.PENDING.value for stage in Stage},
    }
