from __future__ import annotations

from typing import Any

from agents.base import run_stage, workspace_for, write_document
from core import manifest as manifest_util
from core.config import Purpose
from core.logging import bind, get_logger
from core.paths import RunWorkspace, UnsafePathError
from llm import registry
from prompts.developer_json_prompt import get_developer_prompt
from prompts.developer_pdf_prompt import get_developer_doc_prompt
from schema.developer_schema import DeveloperSchema
from state.state import AgentStatus, MultiAgent, Stage
from utils.json_utils import save_json
from utils.status_tracker import log_status, mark

logger = get_logger(__name__)

ARTIFACT_JSON = "developer.json"
ARTIFACT_PDF = "developer.pdf"


async def developer_agent(state: MultiAgent) -> dict[str, Any]:
    """Generate source code, or fix the code it generated last time.

    On a retry the prompt carries three grounded evidence sources: compiler output
    from the static gate, the QA bug list, and real pytest failures.
    """
    stage = Stage.DEVELOPER
    attempt = state.get("retry_count", 0) + 1

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)

        log_status({**state, "retry_count": attempt, "status": mark(state, stage, AgentStatus.IN_PROGRESS)})

        static_report = state.get("static_report") or None
        qa_report = state.get("qa_report") or None
        verification_report = state.get("verification_report") or None

        if attempt > 1:
            logger.info("Fix attempt %d, applying reported failures", attempt)

        model = registry.get_structured_llm(DeveloperSchema, Purpose.HEAVY)
        response = await model.ainvoke(
            get_developer_prompt(
                state["user_requirements"],
                state.get("prd") or {},
                state.get("architecture") or {},
                qa_report=qa_report,
                static_report=static_report,
                verification_report=verification_report,
            )
        )
        output = response.model_dump(mode="json")

        save_json(output, workspace.artifacts / ARTIFACT_JSON)

        manifest = _write_code(output, workspace, dict(state.get("code_manifest") or {}))

        await write_document(
            get_developer_doc_prompt(state["user_requirements"], output), workspace, ARTIFACT_PDF
        )

        logger.info(
            "Wrote %d file(s) across %d service(s)",
            manifest_util.file_count(manifest),
            len(manifest_util.services(manifest)),
        )
        return {
            "code_manifest": manifest,
            "retry_count": attempt,
            # Stale evidence must be cleared so the routers judge only the fresh code.
            "static_report": {},
            "verification_report": {},
        }

    with bind(run_id=state.get("run_id"), stage=stage.value):
        update = await run_stage(state, stage, body)
        # Even on failure the attempt has been spent; recording it keeps the retry
        # cap honest instead of letting a failing node loop forever.
        update.setdefault("retry_count", attempt)
        return update


def _write_code(
    output: dict[str, Any],
    workspace: RunWorkspace,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    rejected = 0

    for service in output.get("services") or []:
        service_name = service.get("service_name") or "unnamed-service"

        for file in service.get("files") or []:
            file_path = file.get("file_path")
            if not file_path:
                continue
            try:
                workspace.write_source_file(service_name, file_path, file.get("code") or "")
            except UnsafePathError as exc:
                rejected += 1
                logger.warning("Rejected generated file path: %s", exc)
                continue

            manifest_util.add_file(
                manifest,
                service_name,
                file_path,
                description=file.get("description", ""),
                language=file.get("language", ""),
            )

    for dependency in output.get("dependency_files") or []:
        file_path = dependency.get("file_path")
        if not file_path:
            continue
        try:
            written = workspace.write_shared_source_file(file_path, dependency.get("code") or "")
        except UnsafePathError as exc:
            rejected += 1
            logger.warning("Rejected dependency file path: %s", exc)
            continue
        logger.info("Wrote dependency file %s", workspace.relative(written))

    readme = output.get("readme_content") or ""
    if readme.strip():
        workspace.write_shared_source_file("README.md", readme)

    if rejected:
        logger.warning("Discarded %d generated path(s) that tried to escape the workspace", rejected)

    return manifest
