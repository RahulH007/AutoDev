"""Shared plumbing for the agent nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core.config import Purpose, Settings, get_settings
from core.logging import get_logger
from core.paths import RunWorkspace
from llm import registry
from llm.accounting import merge_reports, recording
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


async def render_document(
    prompt: Any,
    workspace: RunWorkspace,
    file_name: str,
) -> Path | None:
    """Turn a document prompt into prose and render it to a PDF.

    The whole of PDF production, with no policy attached: one caller reaches it
    automatically during a run and one on an explicit export, and both need the
    identical behaviour. Deciding *whether* to do this belongs to them.

    Best effort in both directions, whoever calls it. A PDF is a restatement of
    a structured artifact that already exists and is already on screen, so a
    failure here loses nothing the reader cannot see and must never propagate:
    not into a stage whose expensive structured call has already succeeded, and
    not into an export request that should simply report that it did not work.
    """
    try:
        text = await registry.allm_call(prompt, Purpose.TEXT)
        return try_save_to_pdf(text, workspace.artifacts / file_name)
    except Exception:
        logger.exception("Could not produce %s", file_name)
        return None


async def write_document(
    prompt: Any,
    workspace: RunWorkspace,
    file_name: str,
    settings: Settings | None = None,
) -> Path | None:
    """Produce a client-facing PDF during a run, if the run was asked to.

    ``GENERATE_PDFS`` governs this path and only this path: whether a *pipeline*
    spends model calls on PDFs while it runs. Off by default, because the console
    renders the PRD and the architecture from the structured JSON — which is the
    canonical artifact either way — so producing them again as prose costs four
    model calls per run against the same per-minute window the code generation
    needs, and buys nothing the reader cannot already see.

    Off does not mean unavailable. `RunService.export_pdf` renders the same
    document on request, whatever this setting says, because that is a user
    asking for a file rather than a pipeline spending on one nobody wanted.
    """
    if not (settings or get_settings()).generate_pdfs:
        return None
    return await render_document(prompt, workspace, file_name)


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

    This is also where a stage's model spending is scoped. ``recording`` binds a
    fresh ledger for the duration of the body, so every call the stage makes —
    the structured call, a QA triage, a PDF — is attributed to this run and this
    stage and to no other. A failed stage is accounted for exactly like a
    successful one: the calls it made were paid for either way.
    """
    run_id = str(state.get("run_id") or "")
    logger.info("%s starting", stage.label)

    with recording(run_id, stage.value) as ledger:
        try:
            update = await body()
        except Exception as exc:
            update = failure_update(state, stage, exc)
        else:
            logger.info("%s complete", stage.label)
            update.setdefault("current_stage", stage.value)
            update.setdefault("status", mark(state, stage, AgentStatus.COMPLETED))

    # Merged rather than replaced: the ledger covers this stage, while the field
    # is the run's running total across stages, retries and review pauses. A
    # resumed run starts a fresh ledger, so the durable figure is this one.
    update["cost_report"] = merge_reports(state.get("cost_report") or {}, ledger.report())
    return update
