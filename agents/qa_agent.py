from __future__ import annotations

from typing import Any

from agents.base import run_stage, workspace_for, write_document
from core import manifest as manifest_util
from core.config import Purpose
from core.logging import bind, get_logger
from core.paths import RunWorkspace, UnsafePathError
from llm import registry
from prompts.qa_json_prompt import get_qa_prompt
from prompts.qa_pdf_prompt import get_qa_doc_prompt
from prompts.qa_triage_prompt import get_qa_triage_prompt
from schema.qa_schema import QASchema
from state.state import AgentStatus, MultiAgent, Stage
from utils.json_utils import extract_json_list, save_json
from utils.status_tracker import log_status, mark

logger = get_logger(__name__)

ARTIFACT_JSON = "qa.json"
ARTIFACT_PDF = "qa.pdf"

# Cap what goes into the review prompt so a large project cannot blow the context
# window. Triage decides which files matter; this is the backstop.
MAX_REVIEW_BYTES = 120_000


async def qa_agent(state: MultiAgent) -> dict[str, Any]:
    """Review the generated code and write tests for it.

    Two passes keep cost down: a cheap triage call reads only the file manifest and
    names the files worth inspecting, then only those files are read from disk and
    sent to the expensive review call.
    """
    stage = Stage.QA

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)
        prd = state.get("prd") or {}
        architecture = state.get("architecture") or {}
        manifest = state.get("code_manifest") or {}

        log_status({**state, "status": mark(state, stage, AgentStatus.IN_PROGRESS)})

        if not manifest:
            raise ValueError("No code manifest to review; the developer agent produced nothing")

        flagged = await _triage(prd, architecture, manifest)
        code = _collect_code(workspace, manifest, flagged)

        model = registry.get_structured_llm(QASchema, Purpose.STRUCTURED)
        response = await model.ainvoke(get_qa_prompt(prd, architecture, manifest, code))
        report = response.model_dump(mode="json")

        save_json(report, workspace.artifacts / ARTIFACT_JSON)
        written = _write_tests(report, workspace)
        await write_document(get_qa_doc_prompt(report), workspace, ARTIFACT_PDF)

        logger.info(
            "Review complete: %d bug(s), %d critical, %d test file(s) written",
            report.get("total_bugs_found", 0),
            report.get("critical_issues", 0),
            written,
        )
        for service in report.get("service_reports") or []:
            logger.info(
                "  %s scored %s/10",
                service.get("service_name", "?"),
                service.get("code_quality_score", "?"),
            )

        return {"qa_report": report}

    with bind(run_id=state.get("run_id"), stage=stage.value):
        return await run_stage(state, stage, body)


async def _triage(prd: dict, architecture: dict, manifest: dict) -> set[str]:
    """Ask a cheap model which files deserve a close look.

    Returns an empty set to mean "review everything", which is what happens if the
    response cannot be parsed or names nothing we recognise.
    """
    known = set(manifest_util.qualified_paths(manifest))

    try:
        raw = await registry.allm_call(get_qa_triage_prompt(prd, architecture, manifest), Purpose.CHEAP)
        requested = {str(item).strip().lstrip("/") for item in extract_json_list(raw)}
    except Exception as exc:
        logger.warning("Triage failed (%s); reviewing every file instead", exc)
        return set()

    flagged = requested & known
    if not flagged:
        logger.warning("Triage named no known files; reviewing every file instead")
        return set()

    logger.info("Triage selected %d of %d file(s) for review", len(flagged), len(known))
    return flagged


def _collect_code(workspace: RunWorkspace, manifest: dict, flagged: set[str]) -> str:
    chunks: list[str] = []
    budget = MAX_REVIEW_BYTES
    skipped = 0

    for slug, entry in manifest_util.iter_files(manifest):
        qualified = f"{slug}/{entry['file_path']}"
        if flagged and qualified not in flagged:
            continue

        try:
            content = workspace.read_source_file(slug, entry["file_path"])
        except (OSError, UnsafePathError) as exc:
            chunks.append(f"\n--- FILE: {qualified} (unreadable: {exc}) ---\n")
            continue

        if len(content) > budget:
            skipped += 1
            continue

        budget -= len(content)
        divider = "=" * 60
        chunks.append(f"\n{divider}\nFILE: {qualified}\n{divider}\n{content}\n")

    if skipped:
        logger.warning("Omitted %d file(s) from the review prompt to stay within budget", skipped)

    return "".join(chunks)


def _write_tests(report: dict, workspace: RunWorkspace) -> int:
    written = 0

    for service in report.get("service_reports") or []:
        service_name = service.get("service_name") or "unnamed-service"

        for case in service.get("test_cases") or []:
            path = (case.get("test_file_path") or "").strip()
            code = case.get("test_code") or ""
            if not path or not code.strip():
                continue

            try:
                workspace.write_test_file(service_name, _normalise_test_path(path), code)
            except UnsafePathError as exc:
                logger.warning("Rejected test file path: %s", exc)
                continue
            written += 1

    return written


def _normalise_test_path(path: str) -> str:
    """Drop a redundant leading ``tests/`` segment.

    Test files already live under the run's tests directory, so a model-supplied
    ``tests/test_auth.py`` would otherwise nest as ``tests/<service>/tests/...``.
    """
    cleaned = path.replace("\\", "/").lstrip("/")
    for prefix in ("tests/", "test/"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :] or "test_generated.py"
    return cleaned
