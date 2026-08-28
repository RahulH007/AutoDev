"""Human-readable pipeline progress."""

from __future__ import annotations

from core.config import get_settings
from core.logging import get_logger
from state.state import AgentStatus, Stage

logger = get_logger(__name__)

_MARKERS = {
    AgentStatus.COMPLETED: "[done]",
    AgentStatus.IN_PROGRESS: "[run ]",
    AgentStatus.FAILED: "[fail]",
    AgentStatus.PENDING: "[    ]",
}


def status_lines(state: dict) -> list[str]:
    status = state.get("status", {}) or {}
    lines: list[str] = []

    retry_count = state.get("retry_count", 0)
    max_retries = get_settings().max_developer_retries
    # The first developer pass is attempt 1, not a retry.
    if retry_count > 1:
        lines.append(f"Retry {retry_count - 1} of {max_retries - 1}: routing back to the Developer agent")

    for stage in Stage:
        value = status.get(stage.value, AgentStatus.PENDING.value)
        try:
            marker = _MARKERS[AgentStatus(value)]
        except ValueError:
            marker = "[ ?  ]"
        lines.append(f"{marker}  {stage.label:<18} {value}")

    return lines


def log_status(state: dict) -> None:
    for line in status_lines(state):
        logger.info(line)


def mark(state: dict, stage: Stage, status: AgentStatus) -> dict[str, str]:
    """Return an updated status map without mutating graph state in place."""
    current = dict(state.get("status", {}) or {})
    current[stage.value] = status.value
    return current
