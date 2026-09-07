"""The developer stage, one architecture service per call.

The whole project in a single response needs roughly 8,200 output tokens, which
cannot fit an 8,000 token-per-minute window at any prompt size. One service
needs three to four thousand and does fit, so the stage makes one call per
service.

Everything here runs against the fake registry: no request leaves the process.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.developer_agent import developer_agent
from core import manifest as manifest_util
from llm.budget import BudgetExceededError
from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode
from state.state import AgentStatus, Stage
from tests import fakes

BACKEND, FRONTEND = "Backend API", "Frontend Web"
BACKEND_SLUG, FRONTEND_SLUG = "backend-api", "frontend-web"


def _service(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} service.",
        "tech_stack": ["Python"],
        "dependencies": [],
        "api_endpoints": [],
        "data_models": [],
    }


def _architecture(*names: str) -> dict[str, Any]:
    return {
        "system_overview": "Two services.",
        "architecture_style": "modular_monolith",
        "services": [_service(name) for name in names],
        "databases": [],
        "environment_variables": [],
    }


def _code_for(service_name: str, file_name: str = "main.py") -> DeveloperSchema:
    return DeveloperSchema(
        project_name="SpendWise",
        services=[
            ServiceCode(
                service_name=service_name,
                files=[
                    CodeFile(
                        file_path=f"app/{file_name}",
                        file_name=file_name,
                        language="python",
                        code=fakes.CALCULATOR_SOURCE,
                        description=f"Entry point for {service_name}.",
                    )
                ],
            )
        ],
    )


class RecordingModel:
    """Returns one canned response per call and remembers what it was asked.

    Standing in for the registry rather than the provider, so the prompt each
    call actually received can be inspected.
    """

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any, config: Any = None) -> Any:
        self.prompts.append(prompt)
        response = self.responses.pop(0) if self.responses else self.responses
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def text_of(self, index: int) -> str:
        from llm.registry import _prompt_text

        return _prompt_text(self.prompts[index])


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):
    def _install(model: RecordingModel) -> RecordingModel:
        from llm import registry

        monkeypatch.setattr(registry, "get_structured_llm", lambda *a, **k: model)
        return model

    return _install


@pytest.fixture
def state(workspace):
    return {
        "run_id": workspace.run_id if hasattr(workspace, "run_id") else "test-run",
        "user_requirements": "Build an expense tracker.",
        "prd": fakes.build_prd().model_dump(mode="json"),
        "architecture": _architecture(BACKEND, FRONTEND),
        "workspace": str(workspace.root),
        "status": {stage.value: AgentStatus.PENDING.value for stage in Stage},
        "retry_count": 0,
    }


class TestOneCallPerService:
    async def test_two_services_produce_two_calls(self, state, install, stub_llm):
        model = install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))

        update = await developer_agent(state)

        assert model.calls == 2
        assert update["generated_services"] == [BACKEND_SLUG, FRONTEND_SLUG]

    async def test_each_call_carries_only_its_own_service(self, state, install, stub_llm):
        model = install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))

        await developer_agent(state)

        first, second = model.text_of(0), model.text_of(1)
        assert BACKEND in first and FRONTEND not in first
        assert FRONTEND in second and BACKEND not in second

    async def test_every_call_still_carries_the_shared_context(self, state, install, stub_llm):
        """The preamble and the PRD projection are needed by every service."""
        model = install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))

        await developer_agent(state)

        for index in (0, 1):
            text = model.text_of(index)
            assert "modular_monolith" in text
            assert "Build an expense tracker." in text

    async def test_service_order_is_preserved(self, state, install, stub_llm):
        install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))

        update = await developer_agent(state)

        assert update["generated_services"] == [BACKEND_SLUG, FRONTEND_SLUG]
        assert manifest_util.services(update["code_manifest"]) == sorted(
            [BACKEND_SLUG, FRONTEND_SLUG]
        )


class TestFilesFromEveryServiceCoexist:
    async def test_both_services_land_in_the_workspace(
        self, state, workspace, install, stub_llm
    ):
        install(RecordingModel(_code_for(BACKEND, "api.py"), _code_for(FRONTEND, "ui.py")))

        update = await developer_agent(state)

        assert (workspace.source / BACKEND_SLUG / "app" / "api.py").is_file()
        assert (workspace.source / FRONTEND_SLUG / "app" / "ui.py").is_file()
        assert manifest_util.file_count(update["code_manifest"]) == 2

    async def test_a_later_service_does_not_erase_an_earlier_one(
        self, state, workspace, install, stub_llm
    ):
        """Each call writes as it arrives; the manifest accumulates."""
        install(RecordingModel(_code_for(BACKEND, "api.py"), _code_for(FRONTEND, "ui.py")))

        update = await developer_agent(state)

        assert manifest_util.files_for(update["code_manifest"], BACKEND_SLUG)
        assert manifest_util.files_for(update["code_manifest"], FRONTEND_SLUG)


class TestRetryResumesRatherThanRestarts:
    async def test_an_already_generated_service_is_not_regenerated(
        self, state, install, stub_llm
    ):
        """Only the missing service is paid for a second time."""
        model = install(RecordingModel(_code_for(FRONTEND)))

        update = await developer_agent(
            {**state, "generated_services": [BACKEND_SLUG], "retry_count": 1}
        )

        assert model.calls == 1
        assert FRONTEND in model.text_of(0)
        assert update["generated_services"] == [BACKEND_SLUG, FRONTEND_SLUG]

    async def test_a_failed_service_is_regenerated(self, state, install, stub_llm):
        model = install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))

        await developer_agent(
            {**state, "generated_services": [], "failed_services": [BACKEND_SLUG], "retry_count": 1}
        )

        assert model.calls == 2

    async def test_evidence_rewrites_everything(self, state, install, stub_llm):
        """A compiler error implicates code that generated fine, so a fix pass
        regenerates every service rather than skipping the ones that exist."""
        model = install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))

        await developer_agent({
            **state,
            "generated_services": [BACKEND_SLUG, FRONTEND_SLUG],
            "retry_count": 1,
            "static_report": {"ran": True, "passed": False, "failures": ["app/api.py:1: E999"]},
        })

        assert model.calls == 2


class TestFailureHandling:
    async def test_one_failed_service_does_not_lose_the_others(self, state, install, stub_llm):
        """A partial failure keeps the work that landed.

        Failing the whole stage used to be the answer, and it threw away a
        service that had just been paid for. The failure is recorded instead, the
        run carries on with what exists, and the next pass targets what is still
        outstanding.
        """
        model = install(RecordingModel(_code_for(BACKEND), RuntimeError("model refused")))

        update = await developer_agent(state)

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.COMPLETED.value
        assert update["generated_services"] == [BACKEND_SLUG]
        assert update["failed_services"] == [FRONTEND_SLUG]
        assert model.calls == 2

    async def test_a_pass_that_produced_nothing_at_all_still_fails(
        self, state, install, stub_llm
    ):
        """There is no project to verify, so this is a failure of the stage."""
        install(RecordingModel(RuntimeError("model refused"), RuntimeError("model refused")))

        update = await developer_agent(state)

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value
        assert update["failed_services"] == [BACKEND_SLUG, FRONTEND_SLUG]

    async def test_the_service_that_worked_is_still_recorded(
        self, state, workspace, install, stub_llm
    ):
        """Partial progress survives so the retry does not pay for it again."""
        install(RecordingModel(_code_for(BACKEND, "api.py"), RuntimeError("model refused")))

        update = await developer_agent(state)

        assert update["generated_services"] == [BACKEND_SLUG]
        assert update["failed_services"] == [FRONTEND_SLUG]
        assert (workspace.source / BACKEND_SLUG / "app" / "api.py").is_file()

    async def test_the_error_names_the_service_that_failed(self, state, install, stub_llm):
        """When nothing landed at all, the error says which services could not."""
        install(RecordingModel(RuntimeError("model refused"), RuntimeError("model refused")))

        update = await developer_agent(state)

        assert BACKEND_SLUG in update["error"]
        assert FRONTEND_SLUG in update["error"]

    async def test_the_attempt_is_recorded_even_when_the_pass_fails(
        self, state, install, stub_llm
    ):
        install(RecordingModel(RuntimeError("no"), RuntimeError("no")))

        update = await developer_agent(state)

        assert update["retry_count"] == 1

    async def test_an_architecture_with_no_services_fails_clearly(
        self, state, install, stub_llm
    ):
        install(RecordingModel())

        update = await developer_agent({**state, "architecture": _architecture()})

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value
        assert "no services" in update["error"].lower()


class TestTheBudgetAppliesToEveryCall:
    async def test_every_service_call_is_charged_to_the_shared_budget(
        self, state, monkeypatch: pytest.MonkeyPatch
    ):
        """Patching the chat model, not the registry, so the real metered ladder
        is built and each call actually passes through the budget."""
        from core.config import LLMProvider, Purpose, get_settings, reset_settings_cache
        from llm import registry
        from llm.budget import budget_for, reset_budgets

        reset_budgets()
        registry.reset_cache()
        reset_settings_cache()

        class StructuredChat:
            """A chat model whose native structured rung answers per service."""

            def __init__(self) -> None:
                self.calls = 0

            def with_structured_output(self, schema, **kwargs):
                from langchain_core.runnables import RunnableLambda

                def answer(_prompt):
                    self.calls += 1
                    return _code_for(BACKEND if self.calls == 1 else FRONTEND)

                return RunnableLambda(answer)

            async def ainvoke(self, prompt, config=None, **kwargs):
                """The PDF pass uses the text path through the same client."""
                from langchain_core.messages import AIMessage

                return AIMessage(content="Developer notes.")

        chat = StructuredChat()
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: chat)

        settings = get_settings()
        model_name = registry.model_name_for(LLMProvider.GOOGLE, Purpose.HEAVY, settings)
        budget = budget_for(LLMProvider.GOOGLE, model_name, settings.llm_tokens_per_minute)

        await developer_agent(state)
        after_two = budget.used()

        assert chat.calls == 2
        # Two prompts were reserved against one shared window, not one.
        assert after_two > 0

        await developer_agent({**state, "generated_services": []})
        assert budget.used() > after_two

    async def test_a_call_too_large_for_the_budget_fails_the_service(
        self, state, install, stub_llm
    ):
        install(RecordingModel(BudgetExceededError("too big"), BudgetExceededError("too big")))

        update = await developer_agent(state)

        assert update["failed_services"] == [BACKEND_SLUG, FRONTEND_SLUG]
        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value
