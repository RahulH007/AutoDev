"""The two verification nodes.

Neither of these calls a language model. They exist so the pipeline's opinion of
the generated code is grounded in what a compiler and a test runner actually say.

Both wrap blocking subprocess work in :func:`asyncio.to_thread`, because the rest
of the graph is async and will eventually run inside the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agents.base import run_stage, workspace_for
from core import manifest as manifest_util
from core.config import get_settings
from core.logging import bind, get_logger
from state.state import AgentStatus, MultiAgent, Stage
from utils.status_tracker import log_status, mark
from verification.static_gate import run_static_gate
from verification.test_runner import run_tests

logger = get_logger(__name__)


async def static_gate_node(state: MultiAgent) -> dict[str, Any]:
    """Parse-check the generated code before anyone pays for a review.

    A syntax error is not a matter of taste, and asking a model to review a file
    that cannot be parsed produces confident nonsense.
    """
    stage = Stage.STATIC_GATE

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)
        log_status({**state, "status": mark(state, stage, AgentStatus.IN_PROGRESS)})

        services = manifest_util.services(state.get("code_manifest") or {}) or None
        report = await asyncio.to_thread(run_static_gate, workspace, services)

        update: dict[str, Any] = {"static_report": report.model_dump(mode="json")}

        if report.passed:
            logger.info("Static gate passed")
            return update

        logger.warning("Static gate failed with %d problem(s)", report.failure_count)
        for failure in report.failures[:10]:
            logger.warning("  %s", failure)

        if _out_of_retries(state):
            # Reviewing code that does not parse would burn a model call for nothing.
            update["error"] = (
                f"Generated code still does not compile after "
                f"{state.get('retry_count', 0)} attempt(s): {report.failures[0]}"
            )

        return update

    with bind(run_id=state.get("run_id"), stage=stage.value):
        return await run_stage(state, stage, body)


async def test_runner_node(state: MultiAgent) -> dict[str, Any]:
    """Execute the tests the QA agent just wrote."""
    stage = Stage.TEST_RUNNER

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)
        log_status({**state, "status": mark(state, stage, AgentStatus.IN_PROGRESS)})

        services = manifest_util.services(state.get("code_manifest") or {}) or None
        report = await asyncio.to_thread(run_tests, workspace, services)

        logger.info("Tests: %s", report.summary)
        for service in report.services:
            if service.error:
                logger.warning("  %s could not run tests: %s", service.service, service.error)
            for failure in service.failures[:5]:
                logger.warning("  FAILED %s", failure.test)

        return {"verification_report": report.model_dump(mode="json")}

    with bind(run_id=state.get("run_id"), stage=stage.value):
        return await run_stage(state, stage, body)


def _out_of_retries(state: MultiAgent) -> bool:
    return state.get("retry_count", 0) >= get_settings().max_developer_retries
