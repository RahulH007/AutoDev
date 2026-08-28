"""The HTTP API.

Runs are long-lived and interactive, so the surface is split three ways: plain
REST for creating and inspecting runs, Server-Sent Events for the live log, and
file responses for the generated code and documents.

Start it with ``python scripts/dev.py``. That wrapper exists because a bare
``uvicorn --reload`` watches ``runs/`` too and restarts on the code the developer
agent generates, killing the run mid-flight.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from core.config import get_settings, shadowed_env_keys
from core.logging import configure_logging, get_logger
from core.paths import UnsafePathError
from graph.checkpoint import open_checkpointer
from server.db import open_database
from server.models import (
    CreateRunRequest,
    FeedbackRequest,
    FileContent,
    FileListResponse,
    FileNode,
    HealthResponse,
    RunDetail,
    RunListResponse,
    RunRecord,
    StageProgress,
)
from server.service import RunNotFoundError, RunService, RunStateError
from state.state import AgentStatus, Stage

logger = get_logger(__name__)

router = APIRouter(prefix="/api")

# How long the SSE generator waits for the next event before rechecking whether
# the run has finished. Short enough to close a stream promptly, long enough to idle.
STREAM_POLL_SECONDS = 1.0

# A moment for the log pump to flush trailing lines after a run reaches its end.
STREAM_DRAIN_SECONDS = 0.3

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".sh": "bash",
    ".toml": "toml",
    ".env": "bash",
    ".txt": "text",
}


def service_of(request: Request) -> RunService:
    service: RunService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - only reachable if lifespan did not run
        raise HTTPException(status_code=503, detail="The service is still starting.")
    return service


# ── Health ───────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", runs=await service_of(request).count())


# ── Runs ─────────────────────────────────────────────────────────


@router.post("/runs", response_model=RunRecord, status_code=201, tags=["runs"])
async def create_run(request: Request, body: CreateRunRequest) -> RunRecord:
    service = service_of(request)
    try:
        record = await service.create(body.requirement, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.auto_start:
        record = await service.begin(record.id)
    return record


@router.get("/runs", response_model=RunListResponse, tags=["runs"])
async def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RunListResponse:
    service = service_of(request)
    return RunListResponse(
        runs=await service.list(limit=limit, offset=offset),
        total=await service.count(),
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
async def get_run(request: Request, run_id: str) -> RunDetail:
    service = service_of(request)
    record = await service.get(run_id)
    state = await service.get_graph_state(run_id)
    workspace = service.workspace(run_id)

    artifacts = (
        sorted(path.name for path in workspace.artifacts.iterdir() if path.is_file())
        if workspace.artifacts.is_dir()
        else []
    )

    return RunDetail(
        run=record,
        stages=_stage_progress(state),
        is_running=service.is_running(run_id),
        prd=state.get("prd") or {},
        architecture=state.get("architecture") or {},
        code_manifest=state.get("code_manifest") or {},
        qa_report=state.get("qa_report") or {},
        static_report=state.get("static_report") or {},
        verification_report=state.get("verification_report") or {},
        artifacts=artifacts,
        has_zip=bool(record.zip_path) and Path(record.zip_path).is_file(),
    )


@router.delete("/runs/{run_id}", status_code=204, tags=["runs"])
async def delete_run(request: Request, run_id: str) -> None:
    await service_of(request).delete(run_id)


# ── Human review ─────────────────────────────────────────────────


@router.post("/runs/{run_id}/approve", response_model=RunRecord, tags=["review"])
async def approve(request: Request, run_id: str) -> RunRecord:
    return await service_of(request).approve(run_id)


@router.post("/runs/{run_id}/feedback", response_model=RunRecord, tags=["review"])
async def submit_feedback(request: Request, run_id: str, body: FeedbackRequest) -> RunRecord:
    return await service_of(request).submit_feedback(run_id, body.feedback)


@router.post("/runs/{run_id}/cancel", response_model=RunRecord, tags=["review"])
async def cancel(request: Request, run_id: str) -> RunRecord:
    return await service_of(request).cancel(run_id)


# ── Log ──────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/log", tags=["events"])
async def read_log(
    request: Request,
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """The whole log so far. Used on page load and as an SSE fallback."""
    events = await service_of(request).events(run_id, after_id=after_id, limit=limit)
    return [event.model_dump(mode="json") for event in events]


@router.get("/runs/{run_id}/events", tags=["events"])
async def stream_events(
    request: Request,
    run_id: str,
    after_id: int = Query(default=0, ge=0),
) -> EventSourceResponse:
    """Live log over SSE.

    History since ``after_id`` is replayed first, so a client that reconnects
    mid-run sees a complete log rather than joining part way through.
    """
    service = service_of(request)
    await service.get(run_id)  # 404 before the stream is opened

    return EventSourceResponse(_events(service, run_id, after_id))


async def _events(service: RunService, run_id: str, after_id: int) -> AsyncIterator[dict[str, str]]:
    last_id = after_id

    async with service.stream(run_id) as queue:
        for event in await service.events(run_id, after_id=after_id):
            last_id = max(last_id, event.id)
            yield {"event": "message", "data": event.model_dump_json()}

        while True:
            record = await service.get(run_id)
            settled = record.status.is_terminal or record.status.is_awaiting_review

            if settled and not service.is_running(run_id):
                # Let the log pump flush the final lines before closing.
                await asyncio.sleep(STREAM_DRAIN_SECONDS)
                while not queue.empty():
                    event = queue.get_nowait()
                    if event.id > last_id:
                        last_id = event.id
                        yield {"event": "message", "data": event.model_dump_json()}

                yield {"event": "end", "data": record.model_dump_json()}
                return

            try:
                event = await asyncio.wait_for(queue.get(), timeout=STREAM_POLL_SECONDS)
            except TimeoutError:
                continue

            if event.id > last_id:
                last_id = event.id
                yield {"event": "message", "data": event.model_dump_json()}


# ── Generated files and artifacts ────────────────────────────────


@router.get("/runs/{run_id}/files", response_model=FileListResponse, tags=["files"])
async def list_files(request: Request, run_id: str) -> FileListResponse:
    service = service_of(request)
    await service.get(run_id)

    return FileListResponse(
        files=[
            FileNode(path=entry.path, size=entry.size, is_generated_test=entry.is_generated_test)
            for entry in service.list_files(run_id)
        ]
    )


@router.get("/runs/{run_id}/files/{file_path:path}", response_model=FileContent, tags=["files"])
async def read_file(request: Request, run_id: str, file_path: str) -> FileContent:
    service = service_of(request)
    await service.get(run_id)

    return FileContent(
        path=file_path,
        language=LANGUAGE_BY_SUFFIX.get(Path(file_path).suffix.lower(), "text"),
        content=service.read_file(run_id, file_path),
    )


@router.get("/runs/{run_id}/artifacts/{name}", tags=["files"])
async def download_artifact(request: Request, run_id: str, name: str) -> FileResponse:
    service = service_of(request)
    await service.get(run_id)

    path = service.artifact_path(run_id, name)
    media_type = "application/pdf" if path.suffix == ".pdf" else "application/json"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/runs/{run_id}/zip", tags=["files"])
async def download_zip(request: Request, run_id: str) -> FileResponse:
    service = service_of(request)
    record = await service.get(run_id)

    archive = await service.package(run_id)
    if archive is None:
        raise HTTPException(status_code=404, detail="This run has not generated any code yet.")

    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{record.name or run_id}.zip".replace("/", "-"),
    )


# ── Error handling ───────────────────────────────────────────────


async def _run_not_found(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"No run with id {exc.args[0]!r}."})


async def _bad_state(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _unsafe_path(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def _missing_file(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"No such file: {exc.args[0]!r}."})


# ── Assembly ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()

    for name in shadowed_env_keys():
        logger.warning(
            "%s is set in this process's environment and overrides the value in your .env file. "
            "The environment value is the one in use. If you are seeing 401s, restart this "
            "process from a shell where the variable is unset.",
            name,
        )

    async with AsyncExitStack() as stack:
        database = await stack.enter_async_context(open_database(settings.database_path))
        checkpointer = await stack.enter_async_context(open_checkpointer(settings.checkpoint_path))

        # Nothing is driving a run that was in flight when the process died.
        await database.reconcile_orphans()

        service = await RunService(database, checkpointer).start()
        stack.push_async_callback(service.stop)

        application.state.service = service
        logger.info("API ready")
        yield


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a bare module-level object, so tests can stand up an
    isolated instance instead of sharing one across the session.
    """
    application = FastAPI(
        title="AgentForge",
        description="Generate a working project from a plain-language requirement.",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)

    application.add_exception_handler(RunNotFoundError, _run_not_found)
    application.add_exception_handler(RunStateError, _bad_state)
    application.add_exception_handler(UnsafePathError, _unsafe_path)
    application.add_exception_handler(FileNotFoundError, _missing_file)
    return application


def _stage_progress(state: dict[str, Any]) -> list[StageProgress]:
    """The pipeline stepper, in execution order."""
    statuses = state.get("status") or {}
    return [
        StageProgress(
            id=stage.value,
            label=stage.label,
            status=statuses.get(stage.value, AgentStatus.PENDING.value),
        )
        for stage in Stage
    ]


app = create_app()
