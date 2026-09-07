"""PDFs as an export, and the structured JSON as the thing on screen.

The pipeline used to describe each document twice: once as the structured JSON
that is the actual artifact, and again as prose so a PDF could be rendered from
it. The second one cost a model call per document, against the same per-minute
window the code generation needs, and produced nothing the console could not
already show from the first.

So the JSON stays canonical and is what the frontend reads, and a PDF is
something a person asks for. What is checked here is that separation: that a
normal run spends nothing on prose, that the structured artifacts are complete
and unchanged without it, that an export produces a PDF when someone wants one,
and that a PDF failing is never allowed to mean a stage failed.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agents.architecture_agent import architecture_agent
from agents.pm_agent import pm_agent
from core.config import get_settings, reset_settings_cache
from server.app import create_app, lifespan
from server.db import open_database
from server.service import EXPORTABLE, RunService, RunStateError
from tests import fakes

REQUIREMENT = "Build an expense tracker with login and monthly reports."


@pytest.fixture
def base_state(workspace):
    return {
        "run_id": workspace.run_id,
        "user_requirements": REQUIREMENT,
        "prd": {},
        "architecture": {},
        "status": {},
    }


@pytest.fixture
async def service(tmp_path, stub_llm):
    async with open_database(tmp_path / "runs.db") as database:
        instance = await RunService(database).start()
        try:
            yield instance
        finally:
            await instance.stop()


@pytest.fixture
async def client(stub_llm):
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            http.app = app
            yield http


async def paused_run(service: RunService):
    """A run driven to its first review gate, so a PRD exists to export."""
    record = await service.create(REQUIREMENT)
    await service.begin(record.id)
    return await service.wait(record.id)


def count_text_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every prose call the registry is asked to make."""
    from llm import registry

    calls: list[Any] = []

    async def record(prompt, *args, **kwargs):
        calls.append(prompt)
        return "# Document\n\nProse.\n"

    monkeypatch.setattr(registry, "allm_call", record)
    return calls


# ── The structured artifacts stand alone ─────────────────────────


class TestStructuredOutputNeedsNoPdf:
    async def test_the_prd_is_produced_without_any_prose_call(
        self, base_state, workspace, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        calls = count_text_calls(monkeypatch)

        update = await pm_agent(base_state)

        assert update["prd"]["product_name"] == "SpendWise"
        assert calls == []

    async def test_the_architecture_is_produced_without_any_prose_call(
        self, base_state, workspace, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        calls = count_text_calls(monkeypatch)
        state = {**base_state, "prd": fakes.build_prd().model_dump(mode="json")}

        update = await architecture_agent(state)

        assert update["architecture"]["architecture_style"] == "modular_monolith"
        assert calls == []

    async def test_the_json_artifact_is_written_either_way(
        self, base_state, workspace, stub_llm
    ):
        await pm_agent(base_state)

        assert (workspace.artifacts / "product_manager.json").is_file()
        assert not (workspace.artifacts / "product_manager.pdf").exists()

    async def test_the_structured_contents_are_the_schema_untouched(
        self, base_state, workspace, stub_llm
    ):
        """The canonical representation is not reshaped for display."""
        update = await pm_agent(base_state)

        assert update["prd"] == fakes.build_prd().model_dump(mode="json")

    async def test_the_architecture_contents_are_the_schema_untouched(
        self, base_state, workspace, stub_llm
    ):
        state = {**base_state, "prd": fakes.build_prd().model_dump(mode="json")}

        update = await architecture_agent(state)

        assert update["architecture"] == fakes.build_architecture().model_dump(mode="json")

    async def test_the_api_serves_the_structured_documents(self, client: AsyncClient):
        response = await client.post("/api/runs", json={"requirement": REQUIREMENT})
        run_id = response.json()["id"]
        await client.app.state.service.wait(run_id)

        detail = (await client.get(f"/api/runs/{run_id}")).json()

        assert detail["prd"]["product_name"] == "SpendWise"
        assert detail["prd"]["features"]
        # One representation, not two: no rendered copy beside the JSON.
        assert "prd_html" not in detail
        assert "prd_markdown" not in detail
        assert "prd_pdf" not in detail


# ── Failures stay cosmetic ───────────────────────────────────────


class TestAPdfFailureIsNotAStageFailure:
    async def test_a_failing_prose_call_leaves_the_prd_intact(
        self, base_state, workspace, stub_llm, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        from llm import registry

        async def boom(*args, **kwargs):
            raise RuntimeError("the provider is rate limited")

        monkeypatch.setattr(registry, "allm_call", boom)

        update = await pm_agent(base_state)

        assert update["prd"]["product_name"] == "SpendWise"
        assert "error" not in update

    async def test_a_failing_prose_call_leaves_the_architecture_intact(
        self, base_state, workspace, stub_llm, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        from llm import registry

        async def boom(*args, **kwargs):
            raise RuntimeError("the provider is rate limited")

        monkeypatch.setattr(registry, "allm_call", boom)
        state = {**base_state, "prd": fakes.build_prd().model_dump(mode="json")}

        update = await architecture_agent(state)

        assert update["architecture"]["architecture_style"] == "modular_monolith"
        assert "error" not in update

    async def test_the_structured_artifact_survives_a_pdf_failure(
        self, base_state, workspace, stub_llm, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        from llm import registry

        async def boom(*args, **kwargs):
            raise RuntimeError("the provider is rate limited")

        monkeypatch.setattr(registry, "allm_call", boom)

        await pm_agent(base_state)

        assert (workspace.artifacts / "product_manager.json").is_file()
        assert not (workspace.artifacts / "product_manager.pdf").exists()

    async def test_a_render_failure_is_swallowed_too(
        self, base_state, workspace, stub_llm, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        """The prose arrived; the renderer is what fell over."""
        monkeypatch.setattr(
            "agents.base.try_save_to_pdf",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )

        update = await pm_agent(base_state)

        assert update["prd"]["product_name"] == "SpendWise"
        assert "error" not in update


# ── The export ───────────────────────────────────────────────────


class TestExport:
    async def test_it_produces_a_pdf_on_request(self, service: RunService):
        record = await paused_run(service)

        name, generated = await service.export_pdf(record.id, "prd")

        assert name == "product_manager.pdf"
        assert generated is True
        assert (service.workspace(record.id).artifacts / name).is_file()

    async def test_it_works_while_automatic_generation_is_off(self, service: RunService):
        """The setting governs the pipeline, not a person asking for a file."""
        assert get_settings().generate_pdfs is False
        record = await paused_run(service)

        _, generated = await service.export_pdf(record.id, "prd")

        assert generated is True

    async def test_it_spends_exactly_one_prose_call(
        self, service: RunService, monkeypatch: pytest.MonkeyPatch
    ):
        record = await paused_run(service)
        calls = count_text_calls(monkeypatch)

        await service.export_pdf(record.id, "prd")

        assert len(calls) == 1

    async def test_exporting_again_costs_nothing(
        self, service: RunService, monkeypatch: pytest.MonkeyPatch
    ):
        """Re-opening the panel must not quietly buy the same document twice."""
        record = await paused_run(service)
        await service.export_pdf(record.id, "prd")

        calls = count_text_calls(monkeypatch)
        name, generated = await service.export_pdf(record.id, "prd")

        assert generated is False
        assert name == "product_manager.pdf"
        assert calls == []

    async def test_the_architecture_can_be_exported_too(self, service: RunService):
        record = await paused_run(service)
        await service.approve(record.id)
        await service.wait(record.id)

        name, generated = await service.export_pdf(record.id, "architecture")

        assert name == "architecture.pdf"
        assert generated is True

    async def test_exporting_a_document_the_run_has_not_produced_is_refused(
        self, service: RunService
    ):
        record = await service.create(REQUIREMENT)

        with pytest.raises(RunStateError, match="no architecture"):
            await service.export_pdf(record.id, "architecture")

    async def test_an_unknown_kind_is_refused(self, service: RunService):
        record = await paused_run(service)

        with pytest.raises(RunStateError, match="cannot be exported"):
            await service.export_pdf(record.id, "qa")

    async def test_the_export_reads_the_canonical_json(self, service: RunService):
        """No second representation: the prompt is built from the graph's own state."""
        record = await paused_run(service)
        state = await service.get_graph_state(record.id)

        assert EXPORTABLE["prd"].state_key == "prd"
        assert state["prd"]["product_name"] == "SpendWise"

    async def test_a_failed_render_reports_rather_than_corrupting_the_run(
        self, service: RunService, monkeypatch: pytest.MonkeyPatch
    ):
        record = await paused_run(service)

        async def boom(*args, **kwargs):
            raise RuntimeError("the provider is rate limited")

        from llm import registry

        monkeypatch.setattr(registry, "allm_call", boom)

        with pytest.raises(RunStateError, match="could not be produced"):
            await service.export_pdf(record.id, "prd")

        # The run itself is untouched: the PRD is still there to read.
        assert (await service.get_graph_state(record.id))["prd"]["product_name"] == "SpendWise"


class TestExportOverHttp:
    async def _paused(self, client: AsyncClient) -> str:
        response = await client.post("/api/runs", json={"requirement": REQUIREMENT})
        run_id = response.json()["id"]
        await client.app.state.service.wait(run_id)
        return run_id

    async def test_the_endpoint_returns_where_to_download_it(self, client: AsyncClient):
        run_id = await self._paused(client)

        response = await client.post(f"/api/runs/{run_id}/artifacts/prd/pdf")

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "product_manager.pdf"
        assert body["generated"] is True
        assert body["url"] == f"/api/runs/{run_id}/artifacts/product_manager.pdf"

    async def test_the_download_link_actually_serves_the_pdf(self, client: AsyncClient):
        run_id = await self._paused(client)
        await client.post(f"/api/runs/{run_id}/artifacts/prd/pdf")

        response = await client.get(f"/api/runs/{run_id}/artifacts/product_manager.pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"

    async def test_a_second_request_reports_that_nothing_was_generated(
        self, client: AsyncClient
    ):
        run_id = await self._paused(client)
        await client.post(f"/api/runs/{run_id}/artifacts/prd/pdf")

        body = (await client.post(f"/api/runs/{run_id}/artifacts/prd/pdf")).json()

        assert body["generated"] is False

    async def test_an_unknown_kind_is_a_client_error(self, client: AsyncClient):
        run_id = await self._paused(client)

        response = await client.post(f"/api/runs/{run_id}/artifacts/nonsense/pdf")

        assert response.status_code == 409

    async def test_an_unknown_run_is_a_404(self, client: AsyncClient):
        assert (await client.post("/api/runs/nope/artifacts/prd/pdf")).status_code == 404

    async def test_the_exported_pdf_appears_in_the_artifact_list(self, client: AsyncClient):
        run_id = await self._paused(client)
        before = (await client.get(f"/api/runs/{run_id}")).json()["artifacts"]

        await client.post(f"/api/runs/{run_id}/artifacts/prd/pdf")
        after = (await client.get(f"/api/runs/{run_id}")).json()["artifacts"]

        assert "product_manager.pdf" not in before
        assert "product_manager.pdf" in after

    async def test_every_existing_detail_field_is_still_present(self, client: AsyncClient):
        run_id = await self._paused(client)

        detail = (await client.get(f"/api/runs/{run_id}")).json()

        assert set(detail) >= {
            "run", "stages", "is_running", "prd", "architecture", "code_manifest",
            "qa_report", "static_report", "verification_report", "service_failures",
            "cost_report", "artifacts", "has_zip",
        }


# ── The legacy automatic path still works ────────────────────────


class TestAutomaticGenerationIsUnchanged:
    """`GENERATE_PDFS=true` still means what it meant: generate during the run."""

    async def test_the_pm_stage_writes_a_pdf_when_asked_to(
        self, base_state, workspace, stub_llm, with_pdfs
    ):
        await pm_agent(base_state)

        assert (workspace.artifacts / "product_manager.pdf").is_file()

    async def test_the_architecture_stage_writes_one_too(
        self, base_state, workspace, stub_llm, with_pdfs
    ):
        state = {**base_state, "prd": fakes.build_prd().model_dump(mode="json")}

        await architecture_agent(state)

        assert (workspace.artifacts / "architecture.pdf").is_file()

    async def test_it_is_still_the_flag_that_decides(
        self, base_state, workspace, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("GENERATE_PDFS", "false")
        reset_settings_cache()
        calls = count_text_calls(monkeypatch)

        await pm_agent(base_state)

        assert calls == []
        assert not (workspace.artifacts / "product_manager.pdf").exists()
