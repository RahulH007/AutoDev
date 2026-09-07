from __future__ import annotations

from typing import Any

from agents.base import run_stage, workspace_for, write_document
from core.config import Purpose
from core.contracts import derive_contract
from core.logging import bind, get_logger
from llm import registry
from prompts.architect_json_prompt import get_architect_prompt
from prompts.architect_pdf_prompt import get_architecture_doc_prompt
from schema.architect_schema import ArchitectSchema
from state.state import MultiAgent, Stage
from utils.json_utils import save_json

logger = get_logger(__name__)

ARTIFACT_JSON = "architecture.json"
ARTIFACT_PDF = "architecture.pdf"


async def architecture_agent(state: MultiAgent) -> dict[str, Any]:
    """Design the system the developer agent will implement."""
    stage = Stage.ARCHITECTURE

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)
        requirement = state["user_requirements"]
        prd = state.get("prd") or {}
        feedback = (state.get("architect_feedback") or "").strip()
        previous = state.get("architecture") or None

        if feedback:
            logger.info("Revising the architecture from reviewer feedback")

        model = registry.get_structured_llm(ArchitectSchema, Purpose.STRUCTURED)
        response = await model.ainvoke(get_architect_prompt(requirement, prd, previous, feedback))
        architecture = response.model_dump(mode="json")

        save_json(architecture, workspace.artifacts / ARTIFACT_JSON)
        await write_document(
            get_architecture_doc_prompt(requirement, architecture), workspace, ARTIFACT_PDF
        )

        # Derived here, from the document this stage has just produced, because
        # this is the only place an architecture is ever written. A revision runs
        # this node again and replaces the contract along with the document, so a
        # contract can never outlive the architecture it came from — and the
        # developer cannot be reached without passing the review gate in between,
        # which is what makes the stored contract the approved one.
        contract = derive_contract(architecture).model_dump(mode="json")

        services = architecture.get("services") or []
        logger.info(
            "Architecture ready: %s with %d service(s): %s",
            architecture.get("architecture_style", "?"),
            len(services),
            ", ".join(s.get("name", "?") for s in services),
        )
        logger.info(
            "Contract: %d service(s), %d required file(s)",
            len(contract["services"]),
            sum(len(s["key_files"]) for s in contract["services"]),
        )
        return {
            "architecture": architecture,
            "contract": contract,
            "architect_feedback": "",
        }

    with bind(run_id=state.get("run_id"), stage=stage.value):
        return await run_stage(state, stage, body)
