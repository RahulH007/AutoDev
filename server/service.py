"""The run service: everything the CLI and the HTTP API both need.

A run is driven by a background task that pushes the LangGraph workflow forward
until it either finishes or interrupts for human review. Interrupts are not
errors; they are the normal resting state of a run waiting for someone to approve
a PRD. The task ends there, and a later ``approve`` or ``submit_feedback`` starts
a fresh one from the checkpoint.

Log lines from the agents are captured by a logging handler, written to the
``run_events`` table, and fanned out to live subscribers. That is what feeds the
terminal in the browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.logging import add_handler, get_logger, remove_handler
from core.paths import RunWorkspace, UnsafePathError, new_run_id, safe_join
from graph.build_graph import build_workflow
from server.broker import EventBroker
from server.db import Database, average_quality_score
from server.models import (
    REVIEW_STATUS_FOR_STAGE,
    EventLevel,
    RunEvent,
    RunRecord,
    RunStatus,
    utcnow,
)
from state.state import FEEDBACK_FIELD, Stage, initial_state
from utils.zip_util import zip_workspace

logger = get_logger(__name__)

# Generous enough for three developer retries through six nodes, plus review loops.
RECURSION_LIMIT = 100

MAX_NAME_LENGTH = 70

# Files that are real but useless to a reader browsing the generated project.
_HIDDEN_DIRS = frozenset({".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"})

_LEVEL_MAP = {
    logging.DEBUG: EventLevel.DEBUG,
    logging.INFO: EventLevel.INFO,
    logging.WARNING: EventLevel.WARNING,
    logging.ERROR: EventLevel.ERROR,
    logging.CRITICAL: EventLevel.ERROR,
}


class RunNotFoundError(LookupError):
    pass


class RunStateError(RuntimeError):
    """The run is not in a state where the requested action makes sense."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    is_generated_test: bool


class _RunLogHandler(logging.Handler):
    """Forwards agent log records onto the service's event queue.

    Records can arrive from a worker thread (the verification layer runs pytest
    through ``asyncio.to_thread``), so the hand-off to the event loop goes through
    ``call_soon_threadsafe``.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[RunEvent]) -> None:
        super().__init__(level=logging.INFO)
        self._loop = loop
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        run_id = getattr(record, "run_id", "-")
        if not run_id or run_id == "-":
            return

        stage = getattr(record, "stage", "")
        event = RunEvent(
            run_id=run_id,
            level=_LEVEL_MAP.get(record.levelno, EventLevel.INFO),
            stage="" if stage == "-" else str(stage),
            message=record.getMessage(),
        )

        with contextlib.suppress(RuntimeError):  # loop already closed during shutdown
            self._loop.call_soon_threadsafe(self._offer, event)

    def _offer(self, event: RunEvent) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(event)


class RunService:
    def __init__(
        self,
        database: Database,
        checkpointer: object | None = None,
        *,
        workflow: Any | None = None,
    ) -> None:
        self.db = database
        self.broker = EventBroker()
        self.workflow = workflow or build_workflow(checkpointer)

        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._log_queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=5000)
        self._handler: _RunLogHandler | None = None
        self._pump: asyncio.Task[None] | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> RunService:
        """Begin capturing agent logs. Call once, when the process comes up."""
        if self._handler is not None:
            return self

        loop = asyncio.get_running_loop()
        self._handler = _RunLogHandler(loop, self._log_queue)
        add_handler(self._handler)
        self._pump = asyncio.create_task(self._drain_logs(), name="run-log-pump")
        return self

    async def stop(self) -> None:
        if self._handler is not None:
            remove_handler(self._handler)
            self._handler = None

        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None

        self.broker.close()

    async def _drain_logs(self) -> None:
        """Persist and fan out captured log records, one at a time and in order."""
        while True:
            event = await self._log_queue.get()
            try:
                stored = await self.db.append_event(
                    event.run_id,
                    event.message,
                    level=event.level,
                    stage=event.stage,
                )
                await self.broker.publish(stored)
            except Exception:  # noqa: BLE001 - logging must never kill the pump
                logger.debug("Could not record a run event", exc_info=True)

    # ── Creating and driving runs ────────────────────────────────

    async def create(self, requirement: str, name: str | None = None) -> RunRecord:
        requirement = (requirement or "").strip()
        if not requirement:
            raise ValueError("A run needs a requirement to build from.")

        run_id = new_run_id()
        workspace = RunWorkspace.create(run_id)

        record = RunRecord(
            id=run_id,
            name=name.strip() if name and name.strip() else _derive_name(requirement),
            requirement=requirement,
            status=RunStatus.QUEUED,
            workspace=str(workspace.root),
        )
        await self.db.create_run(record)
        await self._log(run_id, "Run created.", level=EventLevel.STAGE)
        return record

    async def begin(self, run_id: str) -> RunRecord:
        """Kick off the pipeline from the start."""
        record = await self._require(run_id)
        if record.status is not RunStatus.QUEUED:
            raise RunStateError(f"Run {run_id} has already been started.")

        state = initial_state(run_id, record.requirement)
        return await self._launch(run_id, state)

    async def approve(self, run_id: str) -> RunRecord:
        """Accept the current artifact and continue."""
        record = await self._require(run_id)
        if not record.status.is_awaiting_review:
            raise RunStateError(f"Run {run_id} is not waiting for a review.")

        await self._log(run_id, f"{_stage_label(record)} approved.", level=EventLevel.STAGE)
        return await self._launch(run_id, None)

    async def submit_feedback(self, run_id: str, feedback: str) -> RunRecord:
        """Send the current artifact back to its agent with revision notes."""
        feedback = (feedback or "").strip()
        if not feedback:
            return await self.approve(run_id)

        record = await self._require(run_id)
        if not record.status.is_awaiting_review:
            raise RunStateError(f"Run {run_id} is not waiting for a review.")

        stage = _stage_of(record)
        field = FEEDBACK_FIELD.get(stage)
        if field is None:
            raise RunStateError(f"Stage {record.current_stage!r} does not take feedback.")

        await self.workflow.aupdate_state(self._config(run_id), {field: feedback})
        await self._log(
            run_id,
            f"Revision requested for {_stage_label(record)}: {feedback}",
            level=EventLevel.STAGE,
        )
        return await self._launch(run_id, None)

    async def cancel(self, run_id: str) -> RunRecord:
        record = await self._require(run_id)
        if record.status.is_terminal:
            return record

        task = self._tasks.pop(run_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        await self._log(run_id, "Run cancelled.", level=EventLevel.STAGE)
        return await self._update(run_id, status=RunStatus.CANCELLED, finished_at=utcnow())

    async def wait(self, run_id: str) -> RunRecord:
        """Block until the current leg of the run finishes. Used by the CLI and tests."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
        return await self._require(run_id)

    def is_running(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def _launch(self, run_id: str, graph_input: dict[str, Any] | None) -> RunRecord:
        if self.is_running(run_id):
            raise RunStateError(f"Run {run_id} is already in progress.")

        record = await self._update(run_id, status=RunStatus.RUNNING, error="")
        task = asyncio.create_task(self._drive(run_id, graph_input), name=f"run-{run_id[:8]}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
        return record

    async def _drive(self, run_id: str, graph_input: dict[str, Any] | None) -> None:
        try:
            await self.workflow.ainvoke(graph_input, self._config(run_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Run %s failed", run_id)
            await self._update(
                run_id,
                status=RunStatus.FAILED,
                error=str(exc),
                finished_at=utcnow(),
            )
            return

        await self._settle(run_id)

    async def _settle(self, run_id: str) -> None:
        """Work out where the run came to rest and record it."""
        snapshot = await self.workflow.aget_state(self._config(run_id))
        values: dict[str, Any] = snapshot.values or {}

        fields: dict[str, Any] = {
            "current_stage": values.get("current_stage", ""),
            "retry_count": values.get("retry_count", 0),
        }

        score = average_quality_score(values.get("qa_report"))
        if score is not None:
            fields["qa_score"] = score

        # A PRD gives the run a better name than the truncated requirement.
        product_name = (values.get("prd") or {}).get("product_name")
        if product_name:
            fields["name"] = str(product_name)[:MAX_NAME_LENGTH]

        if snapshot.next:
            fields["status"] = _review_status(values.get("current_stage", ""))
            await self._update(run_id, **fields)
            await self._log(
                run_id,
                f"Paused for review: {fields['status'].label}.",
                level=EventLevel.STAGE,
            )
            return

        error = values.get("error") or ""
        fields["finished_at"] = utcnow()
        fields["error"] = error
        fields["status"] = RunStatus.FAILED if error else RunStatus.COMPLETED

        if not error:
            archive = await asyncio.to_thread(
                zip_workspace, RunWorkspace.for_run(run_id), fields.get("name") or run_id
            )
            if archive is not None:
                fields["zip_path"] = str(archive)

        await self._update(run_id, **fields)
        await self._log(
            run_id,
            f"Run {fields['status'].value}." + (f" {error}" if error else ""),
            level=EventLevel.ERROR if error else EventLevel.STAGE,
        )

    # ── Reading ──────────────────────────────────────────────────

    async def get(self, run_id: str) -> RunRecord:
        return await self._require(run_id)

    async def list(self, limit: int = 50, offset: int = 0) -> list[RunRecord]:
        return await self.db.list_runs(limit=limit, offset=offset)

    async def count(self) -> int:
        return await self.db.count_runs()

    async def get_graph_state(self, run_id: str) -> dict[str, Any]:
        await self._require(run_id)
        snapshot = await self.workflow.aget_state(self._config(run_id))
        return dict(snapshot.values or {})

    async def events(self, run_id: str, after_id: int = 0, limit: int = 1000) -> list[RunEvent]:
        await self._require(run_id)
        return await self.db.list_events(run_id, after_id=after_id, limit=limit)

    @contextlib.asynccontextmanager
    async def stream(self, run_id: str) -> AsyncIterator[asyncio.Queue[RunEvent]]:
        async with self.broker.subscribe(run_id) as queue:
            yield queue

    async def delete(self, run_id: str) -> bool:
        await self.cancel(run_id)
        return await self.db.delete_run(run_id)

    # ── Generated files ──────────────────────────────────────────

    def workspace(self, run_id: str) -> RunWorkspace:
        return RunWorkspace.for_run(run_id)

    def list_files(self, run_id: str) -> list[FileEntry]:
        """Every generated source and test file, as workspace-relative paths."""
        workspace = self.workspace(run_id)
        entries: list[FileEntry] = []

        for root, is_test in ((workspace.source, False), (workspace.tests, True)):
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file() or _HIDDEN_DIRS.intersection(path.parts):
                    continue
                entries.append(
                    FileEntry(
                        path=workspace.relative(path),
                        size=path.stat().st_size,
                        is_generated_test=is_test,
                    )
                )
        return entries

    def read_file(self, run_id: str, relative_path: str) -> str:
        """Read one generated file. The path comes from a URL, so it is untrusted."""
        workspace = self.workspace(run_id)
        target = safe_join(workspace.root, relative_path)

        if not target.is_file():
            raise FileNotFoundError(relative_path)
        if _HIDDEN_DIRS.intersection(target.parts):
            raise UnsafePathError(f"Not a browsable file: {relative_path}")

        return target.read_text(encoding="utf-8", errors="replace")

    def artifact_path(self, run_id: str, name: str) -> Path:
        workspace = self.workspace(run_id)
        target = safe_join(workspace.artifacts, name)
        if not target.is_file():
            raise FileNotFoundError(name)
        return target

    async def package(self, run_id: str) -> Path | None:
        """Build (or rebuild) the downloadable archive for a run."""
        record = await self._require(run_id)

        if record.zip_path and Path(record.zip_path).is_file():
            return Path(record.zip_path)

        archive = await asyncio.to_thread(
            zip_workspace, self.workspace(run_id), record.name or run_id
        )
        if archive is not None:
            await self._update(run_id, zip_path=str(archive))
        return archive

    # ── Internals ────────────────────────────────────────────────

    def _config(self, run_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": run_id},
            "recursion_limit": RECURSION_LIMIT,
        }

    async def _require(self, run_id: str) -> RunRecord:
        record = await self.db.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    async def _update(self, run_id: str, **fields: Any) -> RunRecord:
        record = await self.db.update_run(run_id, **fields)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    async def _log(self, run_id: str, message: str, *, level: EventLevel = EventLevel.INFO) -> None:
        """Record a service-level event, as opposed to one captured from a logger."""
        event = await self.db.append_event(run_id, message, level=level)
        await self.broker.publish(event)


# ── Helpers ──────────────────────────────────────────────────────


def _derive_name(requirement: str) -> str:
    """A readable placeholder until the PM agent names the product."""
    first_line = requirement.strip().splitlines()[0].strip()
    if len(first_line) <= MAX_NAME_LENGTH:
        return first_line
    return first_line[: MAX_NAME_LENGTH - 1].rstrip() + "…"


def _stage_of(record: RunRecord) -> Stage | None:
    try:
        return Stage(record.current_stage)
    except ValueError:
        return None


def _stage_label(record: RunRecord) -> str:
    stage = _stage_of(record)
    return stage.label if stage else "The current stage"


def _review_status(current_stage: str) -> RunStatus:
    """Which review a paused run is waiting on.

    Read from the stage the graph reports rather than guessed from which
    artifacts happen to be populated.
    """
    try:
        stage = Stage(current_stage)
    except ValueError:
        return RunStatus.AWAITING_PM_REVIEW
    return REVIEW_STATUS_FOR_STAGE.get(stage, RunStatus.AWAITING_PM_REVIEW)
