"""The shapes the API and the database agree on."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from state.state import Stage


def utcnow() -> str:
    """ISO-8601 in UTC. Stored as text because SQLite has no date type."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_PM_REVIEW = "awaiting_pm_review"
    AWAITING_ARCHITECTURE_REVIEW = "awaiting_architecture_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

    @property
    def is_awaiting_review(self) -> bool:
        return self in (
            RunStatus.AWAITING_PM_REVIEW,
            RunStatus.AWAITING_ARCHITECTURE_REVIEW,
        )

    @property
    def label(self) -> str:
        return {
            RunStatus.QUEUED: "Queued",
            RunStatus.RUNNING: "Running",
            RunStatus.AWAITING_PM_REVIEW: "Awaiting PRD review",
            RunStatus.AWAITING_ARCHITECTURE_REVIEW: "Awaiting architecture review",
            RunStatus.COMPLETED: "Completed",
            RunStatus.FAILED: "Failed",
            RunStatus.CANCELLED: "Cancelled",
        }[self]


# Which review a paused run is waiting on, keyed by the stage that just finished.
REVIEW_STATUS_FOR_STAGE: dict[Stage, RunStatus] = {
    Stage.PM: RunStatus.AWAITING_PM_REVIEW,
    Stage.ARCHITECTURE: RunStatus.AWAITING_ARCHITECTURE_REVIEW,
}


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    STAGE = "stage"  # a stage transition rather than a log line


class RunEvent(BaseModel):
    id: int = 0
    run_id: str
    ts: str = Field(default_factory=utcnow)
    level: EventLevel = EventLevel.INFO
    stage: str = ""
    message: str = ""


class RunRecord(BaseModel):
    """One row of the ``runs`` table."""

    id: str
    name: str
    requirement: str
    status: RunStatus = RunStatus.QUEUED
    current_stage: str = ""
    retry_count: int = 0
    qa_score: float | None = None
    workspace: str = ""
    zip_path: str = ""
    error: str = ""
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    finished_at: str = ""


# ── API request and response bodies ──────────────────────────────


class CreateRunRequest(BaseModel):
    requirement: str = Field(min_length=1, description="What to build, in plain language.")
    name: str | None = Field(default=None, description="Optional label for the run.")
    auto_start: bool = Field(default=True, description="Begin the pipeline immediately.")


class FeedbackRequest(BaseModel):
    feedback: str = Field(default="", description="Revision notes. Empty means approve.")


class StageProgress(BaseModel):
    id: str
    label: str
    status: str


class RunDetail(BaseModel):
    """A run plus everything the detail page renders."""

    run: RunRecord
    stages: list[StageProgress] = Field(default_factory=list)
    is_running: bool = False
    prd: dict = Field(default_factory=dict)
    architecture: dict = Field(default_factory=dict)
    code_manifest: dict = Field(default_factory=dict)
    qa_report: dict = Field(default_factory=dict)
    static_report: dict = Field(default_factory=dict)
    verification_report: dict = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    has_zip: bool = False


class RunListResponse(BaseModel):
    runs: list[RunRecord]
    total: int
    limit: int
    offset: int


class FileNode(BaseModel):
    path: str
    size: int
    is_generated_test: bool


class FileListResponse(BaseModel):
    files: list[FileNode]


class FileContent(BaseModel):
    path: str
    language: str
    content: str


class HealthResponse(BaseModel):
    status: str = "ok"
    runs: int = 0
