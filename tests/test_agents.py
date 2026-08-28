from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.architecture_agent import architecture_agent
from agents.developer_agent import developer_agent
from agents.pm_agent import pm_agent
from agents.qa_agent import qa_agent
from core import manifest as manifest_util
from core.paths import RunWorkspace
from schema.developer_schema import DeveloperSchema
from schema.product_manager_schema import ManagerSchema
from schema.qa_schema import QASchema
from state.state import AgentStatus, Stage, initial_state
from tests import fakes

REQUIREMENT = "Build an expense tracker with login and monthly reports."


@pytest.fixture
def base_state(workspace: RunWorkspace):
    return initial_state(workspace.run_id, REQUIREMENT)


class TestPmAgent:
    async def test_produces_prd_and_artifacts(self, base_state, workspace, stub_llm):
        update = await pm_agent(base_state)

        assert update["prd"]["product_name"] == "SpendWise"
        assert update["current_stage"] == Stage.PM.value
        assert update["status"][Stage.PM.value] == AgentStatus.COMPLETED.value

        saved = json.loads((workspace.artifacts / "product_manager.json").read_text(encoding="utf-8"))
        assert saved["product_name"] == "SpendWise"
        assert (workspace.artifacts / "product_manager.pdf").is_file()

    async def test_enums_are_serialised_as_plain_strings(self, base_state, stub_llm):
        update = await pm_agent(base_state)
        # A (str, Enum) member would render as "Priority.HIGH" when displayed.
        assert update["prd"]["features"][0]["priority"] == "high"
        assert update["prd"]["complexity_estimate"] == "medium"

    async def test_clears_feedback_so_the_review_loop_terminates(self, base_state, stub_llm):
        update = await pm_agent({**base_state, "pm_feedback": "Add budget alerts"})
        assert update["pm_feedback"] == ""

    async def test_revision_prompt_includes_previous_prd_and_feedback(self, base_state, stub_llm):
        previous = fakes.build_prd("Old Name").model_dump(mode="json")
        await pm_agent({**base_state, "prd": previous, "pm_feedback": "Rename it"})

        rendered = "\n".join(fakes._render(p) for p in stub_llm.prompts)
        assert "Rename it" in rendered
        assert "Old Name" in rendered

    async def test_first_pass_prompt_has_no_revision_section(self, base_state, stub_llm):
        await pm_agent(base_state)
        rendered = "\n".join(fakes._render(p) for p in stub_llm.prompts)
        assert "USER REVISION FEEDBACK" not in rendered

    async def test_model_failure_is_reported_not_raised(self, base_state, stub_llm, monkeypatch):
        def explode(*_args, **_kwargs):
            raise RuntimeError("provider is down")

        monkeypatch.setattr("llm.registry.get_structured_llm", explode)
        update = await pm_agent(base_state)

        assert update["status"][Stage.PM.value] == AgentStatus.FAILED.value
        assert "provider is down" in update["error"]


class TestArchitectureAgent:
    async def test_produces_architecture_and_artifacts(self, base_state, workspace, stub_llm):
        state = {**base_state, "prd": fakes.build_prd().model_dump(mode="json")}
        update = await architecture_agent(state)

        assert update["architecture"]["architecture_style"] == "modular_monolith"
        assert update["architect_feedback"] == ""
        assert (workspace.artifacts / "architecture.json").is_file()
        assert (workspace.artifacts / "architecture.pdf").is_file()


class TestDeveloperAgent:
    @pytest.fixture
    def ready_state(self, base_state):
        return {
            **base_state,
            "prd": fakes.build_prd().model_dump(mode="json"),
            "architecture": fakes.build_architecture().model_dump(mode="json"),
        }

    async def test_writes_source_into_the_slugged_service_directory(self, ready_state, workspace, stub_llm):
        update = await developer_agent(ready_state)

        service_dir = workspace.source / fakes.SERVICE_SLUG
        assert (service_dir / "app" / "calculator.py").is_file()
        assert (service_dir / "app" / "store.py").is_file()
        assert update["retry_count"] == 1

    async def test_manifest_is_keyed_by_slug_and_keeps_the_display_name(self, ready_state, stub_llm):
        update = await developer_agent(ready_state)
        manifest = update["code_manifest"]

        assert manifest_util.services(manifest) == [fakes.SERVICE_SLUG]
        assert manifest_util.display_name(manifest, fakes.SERVICE_SLUG) == fakes.SERVICE_NAME
        assert f"{fakes.SERVICE_SLUG}/app/calculator.py" in manifest_util.qualified_paths(manifest)

    async def test_writes_dependency_files_and_readme(self, ready_state, workspace, stub_llm):
        await developer_agent(ready_state)
        assert (workspace.source / "requirements.txt").is_file()
        assert (workspace.source / "README.md").read_text(encoding="utf-8").startswith("# SpendWise")

    async def test_path_traversal_is_rejected_without_failing_the_run(
        self, ready_state, workspace, stub_llm, tmp_path: Path
    ):
        stub_llm.set(DeveloperSchema, fakes.build_traversal_developer_output())

        update = await developer_agent(ready_state)

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.COMPLETED.value
        assert not (tmp_path / "escaped.py").exists()
        assert not (workspace.root.parent / "escaped.py").exists()
        # The hostile file is the only one, so nothing was recorded.
        assert manifest_util.file_count(update["code_manifest"]) == 0

    async def test_retry_merges_into_the_existing_manifest(self, ready_state, stub_llm):
        first = await developer_agent(ready_state)
        assert manifest_util.file_count(first["code_manifest"]) == 3

        # A fix pass emits only the file it changed.
        patched = DeveloperSchema(
            project_name="SpendWise",
            services=[
                fakes.ServiceCode(
                    service_name=fakes.SERVICE_NAME,
                    files=[
                        fakes.CodeFile(
                            file_path="app/calculator.py",
                            file_name="calculator.py",
                            language="python",
                            code=fakes.CALCULATOR_SOURCE,
                            description="Fixed arithmetic helpers.",
                        )
                    ],
                )
            ],
        )
        stub_llm.set(DeveloperSchema, patched)

        second = await developer_agent({**ready_state, **first})

        assert second["retry_count"] == 2
        # Untouched files survive, and the changed one is updated rather than duplicated.
        assert manifest_util.file_count(second["code_manifest"]) == 3
        entries = manifest_util.files_for(second["code_manifest"], fakes.SERVICE_SLUG)
        calculator = next(e for e in entries if e["file_path"] == "app/calculator.py")
        assert calculator["description"] == "Fixed arithmetic helpers."

    async def test_stale_evidence_is_cleared_after_a_fix_pass(self, ready_state, stub_llm):
        state = {
            **ready_state,
            "static_report": {"passed": False, "failures": ["old failure"]},
            "verification_report": {"passed": False, "services": []},
        }
        update = await developer_agent(state)
        assert update["static_report"] == {}
        assert update["verification_report"] == {}

    async def test_failure_still_consumes_the_attempt(self, ready_state, stub_llm, monkeypatch):
        def explode(*_args, **_kwargs):
            raise RuntimeError("context length exceeded")

        monkeypatch.setattr("llm.registry.get_structured_llm", explode)
        update = await developer_agent(ready_state)

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value
        # Without this the retry cap could never be reached and the graph would spin.
        assert update["retry_count"] == 1

    async def test_fix_prompt_carries_real_failure_evidence(self, ready_state, stub_llm):
        state = {
            **ready_state,
            "static_report": {"passed": False, "failures": ["backend-api/app/x.py:2 SyntaxError: bad"]},
            "verification_report": {
                "passed": False,
                "services": [
                    {
                        "service": "backend-api",
                        "passed": 1,
                        "failed": 1,
                        "errors": 0,
                        "failures": [{"test": "test_add", "message": "assert 5 == 6"}],
                    }
                ],
            },
            "qa_report": fakes.build_qa_report(score=4, critical_issues=1).model_dump(mode="json"),
        }
        await developer_agent(state)

        rendered = "\n".join(fakes._render(p) for p in stub_llm.prompts)
        assert "SyntaxError: bad" in rendered
        assert "assert 5 == 6" in rendered
        assert "divide by zero" in rendered.lower() or "denominator" in rendered
        assert "FIX MODE" in rendered


class TestQaAgent:
    @pytest.fixture
    async def coded_state(self, base_state, stub_llm):
        state = {
            **base_state,
            "prd": fakes.build_prd().model_dump(mode="json"),
            "architecture": fakes.build_architecture().model_dump(mode="json"),
        }
        return {**state, **await developer_agent(state)}

    async def test_produces_report_and_writes_tests(self, coded_state, workspace, stub_llm):
        update = await qa_agent(coded_state)

        assert update["qa_report"]["critical_issues"] == 0
        assert (workspace.artifacts / "qa.json").is_file()
        assert (workspace.tests / fakes.SERVICE_SLUG / "test_calculator.py").is_file()

    async def test_triage_limits_which_files_are_read(self, coded_state, stub_llm):
        await qa_agent(coded_state)

        review_prompt = "\n".join(fakes._render(p) for p in stub_llm.prompts)
        # Triage flags only calculator.py, so store.py must not reach the review call.
        assert "FILE: backend-api/app/calculator.py" in review_prompt
        assert "FILE: backend-api/app/store.py" not in review_prompt

    async def test_unparseable_triage_falls_back_to_reviewing_everything(
        self, coded_state, stub_llm, monkeypatch
    ):
        async def broken_triage(prompt, purpose=None, settings=None):
            return "I cannot comply with that request."

        monkeypatch.setattr("llm.registry.allm_call", broken_triage)
        await qa_agent(coded_state)

        review_prompt = "\n".join(fakes._render(p) for p in stub_llm.prompts)
        assert "FILE: backend-api/app/calculator.py" in review_prompt
        assert "FILE: backend-api/app/store.py" in review_prompt

    async def test_redundant_tests_prefix_is_stripped(self, coded_state, workspace, stub_llm):
        report = fakes.build_qa_report()
        report.service_reports[0].test_cases[0].test_file_path = "tests/test_auth.py"
        stub_llm.set(QASchema, report)

        await qa_agent(coded_state)

        assert (workspace.tests / fakes.SERVICE_SLUG / "test_auth.py").is_file()
        assert not (workspace.tests / fakes.SERVICE_SLUG / "tests").exists()

    async def test_empty_manifest_is_a_reported_failure(self, base_state, stub_llm):
        update = await qa_agent({**base_state, "prd": {}, "architecture": {}, "code_manifest": {}})
        assert update["status"][Stage.QA.value] == AgentStatus.FAILED.value
        assert "manifest" in update["error"].lower()


async def test_agents_never_import_a_provider(monkeypatch, base_state):
    """The stub is not installed here: reaching a provider would raise, not hang."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from core.config import reset_settings_cache

    reset_settings_cache()

    update = await pm_agent(base_state)
    assert update["status"][Stage.PM.value] == AgentStatus.FAILED.value
    assert "GOOGLE_API_KEY" in update["error"]


def test_manifest_helpers_round_trip():
    manifest: dict = {}
    manifest_util.add_file(manifest, "Backend API", "app/main.py", description="entry", language="python")
    manifest_util.add_file(manifest, "backend api", "app/util.py", description="helpers")

    assert manifest_util.services(manifest) == ["backend-api"]
    assert manifest_util.file_count(manifest) == 2
    assert "backend-api/app/main.py" in manifest_util.qualified_paths(manifest)
    assert "entry" in manifest_util.summarise(manifest)


def test_prd_contract_is_stable_for_the_stub():
    assert ManagerSchema.model_validate(fakes.build_prd().model_dump(mode="json"))
