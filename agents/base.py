"""Shared plumbing for the agent nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core.config import Purpose
from core.logging import get_logger
from core.paths import RunWorkspace
from llm import registry
from state.state import AgentStatus, MultiAgent, Stage
from utils.pdf_util import try_save_to_pdf
from utils.status_tracker import mark

logger = get_logger(__name__)


def workspace_for(state: MultiAgent) -> RunWorkspace:
    run_id = state.get("run_id")
    if not run_id:
        raise ValueError(
            "State has no run_id. Build initial state with state.initial_state() "
            "or start the run through RunService."
        )
    return RunWorkspace.for_run(run_id).ensure()


async def write_document(prompt: Any, workspace: RunWorkspace, file_name: str) -> Path | None:
    """Generate client-facing prose and render it to a PDF.

    Best effort: a PDF problem must never fail a run that has already paid for the
    expensive structured call.
    """
    text = await registry.allm_call(prompt, Purpose.TEXT)
    return try_save_to_pdf(text, workspace.artifacts / file_name)


def failure_update(state: MultiAgent, stage: Stage, exc: Exception) -> dict[str, Any]:
    """Uniform state update for a node that could not complete."""
    logger.exception("%s failed: %s", stage.label, exc)
    return {
        "status": mark(state, stage, AgentStatus.FAILED),
        "current_stage": stage.value,
        "error": f"{stage.label} failed: {exc}",
    }


async def run_stage(
    state: MultiAgent,
    stage: Stage,
    body: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute a node body, converting any exception into a FAILED status.

    Nodes must not raise: the graph has to stay resumable so a partially complete
    run can be inspected and retried rather than lost.
    """
    logger.info("%s starting", stage.label)
    try:
        update = await body()
    except Exception as exc:
        return failure_update(state, stage, exc)

    logger.info("%s complete", stage.label)
    update.setdefault("current_stage", stage.value)
    update.setdefault("status", mark(state, stage, AgentStatus.COMPLETED))
    return update
