from __future__ import annotations

from typing import Any

from agents.base import run_stage, workspace_for, write_document
from core.config import Purpose
from core.logging import bind, get_logger
from llm import registry
from prompts.pm_json_prompt import get_pm_prompt
from prompts.pm_pdf_prompt import get_pm_doc_prompt
from schema.product_manager_schema import ManagerSchema
from state.state import MultiAgent, Stage
from utils.json_utils import save_json

logger = get_logger(__name__)

ARTIFACT_JSON = "product_manager.json"
ARTIFACT_PDF = "product_manager.pdf"


async def pm_agent(state: MultiAgent) -> dict[str, Any]:
    """Turn the raw requirement into a structured PRD.

    On a revision pass the previous PRD and the reviewer's feedback are both fed
    back in, so the model edits its own work instead of starting over.
    """
    stage = Stage.PM

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)
        requirement = state["user_requirements"]
        feedback = (state.get("pm_feedback") or "").strip()
        previous = state.get("prd") or None

        if feedback:
            logger.info("Revising the PRD from reviewer feedback")

        model = registry.get_structured_llm(ManagerSchema, Purpose.STRUCTURED)
        response = await model.ainvoke(get_pm_prompt(requirement, previous, feedback))
        prd = response.model_dump(mode="json")

        save_json(prd, workspace.artifacts / ARTIFACT_JSON)
        await write_document(get_pm_doc_prompt(requirement, prd), workspace, ARTIFACT_PDF)

        logger.info(
            "PRD ready: %s with %d features",
            prd.get("product_name", "(unnamed)"),
            len(prd.get("features") or []),
        )
        # Clearing the feedback is what lets the review router stop looping.
        return {"prd": prd, "pm_feedback": ""}

    with bind(run_id=state.get("run_id"), stage=stage.value):
        return await run_stage(state, stage, body)
