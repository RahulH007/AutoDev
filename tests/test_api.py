"""API integration tests.

The whole stack runs: FastAPI, the run service, SQLite, LangGraph and the real
verification subprocesses. Only the language model is stubbed.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from schema.developer_schema import DeveloperSchema
from server.app import create_app, lifespan
from server.models import RunStatus
from state.state import Stage
from tests import fakes

REQUIREMENT = "Build an expense tracker with login and monthly reports."


@pytest.fixture
async def client(stub_llm):
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            http.app = app
            yield http


async def create_run(client: AsyncClient, requirement: str = REQUIREMENT, **body) -> dict:
    response = await client.post("/api/runs", json={"requirement": requirement, **body})
    assert response.status_code == 201, response.text
    return response.json()


async def settle(client: AsyncClient, run_id: str) -> dict:
    """Wait for the current leg of the run, then return the detail payload.

    Also waits for the log pump to stop writing, so tests that assert on the
    event log are not racing it.
    """
    service = client.app.state.service
    await service.wait(run_id)

    previous = -1
    for _ in range(100):
        await asyncio.sleep(0.02)
        current = len(await service.events(run_id))
        if current == previous:
            break
        previous = current

    response = await client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def run_to_completion(client: AsyncClient, requirement: str = REQUIREMENT) -> dict:
    run = await create_run(client, requirement)
    detail = await settle(client, run["id"])

    for _ in range(3):
        if not RunStatus(detail["run"]["status"]).is_awaiting_review:
            break
        await client.post(f"/api/runs/{run['id']}/approve")
        detail = await settle(client, run["id"])

    return detail


# ─────────────────────────────────────────────────────────────────
# Meta
# ─────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_it_reports_ok(self, client: AsyncClient):
        response = await client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "runs": 0}

    async def test_it_counts_runs(self, client: AsyncClient):
        await create_run(client, auto_start=False)
        assert (await client.get("/api/health")).json()["runs"] == 1

    async def test_the_schema_is_published(self, client: AsyncClient):
        schema = (await client.get("/openapi.json")).json()
        assert "/api/runs" in schema["paths"]
        assert "/api/runs/{run_id}/events" in schema["paths"]


# ─────────────────────────────────────────────────────────────────
# Creating and listing
# ─────────────────────────────────────────────────────────────────


class TestCreateRun:
    async def test_a_run_is_created_and_started(self, client: AsyncClient):
        run = await create_run(client)

        assert run["requirement"] == REQUIREMENT
        assert run["status"] in (RunStatus.RUNNING, RunStatus.AWAITING_PM_REVIEW)
        assert run["id"]

    async def test_it_can_be_created_without_starting(self, client: AsyncClient):
        run = await create_run(client, auto_start=False)
        assert run["status"] == RunStatus.QUEUED

    async def test_an_empty_requirement_is_rejected(self, client: AsyncClient):
        response = await client.post("/api/runs", json={"requirement": ""})
        assert response.status_code == 422

    async def test_a_missing_body_is_rejected(self, client: AsyncClient):
        assert (await client.post("/api/runs", json={})).status_code == 422

    async def test_a_custom_name_is_kept(self, client: AsyncClient):
        run = await create_run(client, name="My Tracker", auto_start=False)
        assert run["name"] == "My Tracker"


class TestListRuns:
    async def test_an_empty_list_is_not_an_error(self, client: AsyncClient):
        body = (await client.get("/api/runs")).json()
        assert body == {"runs": [], "total": 0, "limit": 50, "offset": 0}

    async def test_runs_are_listed(self, client: AsyncClient):
        await create_run(client, auto_start=False)
        await create_run(client, "Build a blog.", auto_start=False)

        body = (await client.get("/api/runs")).json()
        assert body["total"] == 2
        assert len(body["runs"]) == 2

    async def test_paging_is_honoured(self, client: AsyncClient):
        for _ in range(3):
            await create_run(client, auto_start=False)

        body = (await client.get("/api/runs?limit=2&offset=1")).json()
        assert len(body["runs"]) == 2
        assert body["total"] == 3

    async def test_a_silly_page_size_is_rejected(self, client: AsyncClient):
        assert (await client.get("/api/runs?limit=0")).status_code == 422
        assert (await client.get("/api/runs?limit=9999")).status_code == 422


class TestGetRun:
    async def test_an_unknown_run_is_a_404(self, client: AsyncClient):
        response = await client.get("/api/runs/nope")

        assert response.status_code == 404
        assert "nope" in response.json()["detail"]

    async def test_the_detail_carries_the_pipeline_stepper(self, client: AsyncClient):
        run = await create_run(client, auto_start=False)
        detail = (await client.get(f"/api/runs/{run['id']}")).json()

        assert [stage["id"] for stage in detail["stages"]] == [stage.value for stage in Stage]
        assert detail["stages"][0]["label"] == "Product Manager"

    async def test_a_deleted_run_is_gone(self, client: AsyncClient):
        run = await create_run(client, auto_start=False)

        assert (await client.delete(f"/api/runs/{run['id']}")).status_code == 204
        assert (await client.get(f"/api/runs/{run['id']}")).status_code == 404


# ─────────────────────────────────────────────────────────────────
# Review gates
# ─────────────────────────────────────────────────────────────────


class TestReview:
    async def test_the_run_pauses_with_a_prd_to_read(self, client: AsyncClient):
        run = await create_run(client)
        detail = await settle(client, run["id"])

        assert detail["run"]["status"] == RunStatus.AWAITING_PM_REVIEW
        assert detail["prd"]["product_name"] == "SpendWise"
        assert detail["prd"]["features"]
        assert not detail["architecture"]

    async def test_approving_advances_to_the_architecture(self, client: AsyncClient):
        run = await create_run(client)
        await settle(client, run["id"])

        response = await client.post(f"/api/runs/{run['id']}/approve")
        assert response.status_code == 200

        detail = await settle(client, run["id"])
        assert detail["run"]["status"] == RunStatus.AWAITING_ARCHITECTURE_REVIEW
        assert detail["architecture"]["services"]

    async def test_feedback_sends_the_prd_back(self, client: AsyncClient, stub_llm):
        run = await create_run(client)
        await settle(client, run["id"])

        response = await client.post(
            f"/api/runs/{run['id']}/feedback",
            json={"feedback": "Add budget alerts"},
        )
        assert response.status_code == 200

        detail = await settle(client, run["id"])
        assert detail["run"]["status"] == RunStatus.AWAITING_PM_REVIEW
        assert "Add budget alerts" in fakes.render_all(stub_llm.prompts)

    async def test_approving_a_run_that_is_not_paused_is_a_conflict(self, client: AsyncClient):
        run = await create_run(client, auto_start=False)
        response = await client.post(f"/api/runs/{run['id']}/approve")

        assert response.status_code == 409
        assert "not waiting" in response.json()["detail"]

    async def test_approving_an_unknown_run_is_a_404(self, client: AsyncClient):
        assert (await client.post("/api/runs/nope/approve")).status_code == 404

    async def test_a_run_can_be_cancelled(self, client: AsyncClient):
        run = await create_run(client)
        await settle(client, run["id"])

        response = await client.post(f"/api/runs/{run['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == RunStatus.CANCELLED


# ─────────────────────────────────────────────────────────────────
# A complete run
# ─────────────────────────────────────────────────────────────────


class TestCompleteRun:
    async def test_it_finishes_and_reports_everything(self, client: AsyncClient):
        detail = await run_to_completion(client)

        assert detail["run"]["status"] == RunStatus.COMPLETED
        assert detail["run"]["qa_score"] == 9.0
        assert detail["prd"]["product_name"] == "SpendWise"
        assert detail["architecture"]["services"]
        assert detail["code_manifest"]
        assert detail["qa_report"]["passed"]

    async def test_the_verification_results_are_exposed(self, client: AsyncClient):
        detail = await run_to_completion(client)

        assert detail["static_report"]["passed"]
        assert detail["verification_report"]["passed"]
        assert "4 passed" in detail["verification_report"]["summary"]

    async def test_every_stage_shows_as_complete(self, client: AsyncClient):
        detail = await run_to_completion(client)
        assert {stage["status"] for stage in detail["stages"]} == {"COMPLETED"}

    async def test_a_broken_build_is_reported_as_failed(
        self, client: AsyncClient, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        from core.config import reset_settings_cache

        monkeypatch.setenv("MAX_DEVELOPER_RETRIES", "1")
        reset_settings_cache()
        stub_llm.set(DeveloperSchema, fakes.build_developer_output(broken=True))

        detail = await run_to_completion(client)

        assert detail["run"]["status"] == RunStatus.FAILED
        assert "does not compile" in detail["run"]["error"]
        assert not detail["static_report"]["passed"]


# ─────────────────────────────────────────────────────────────────
# Log and events
# ─────────────────────────────────────────────────────────────────


class TestLog:
    async def test_the_log_is_readable(self, client: AsyncClient):
        run = await create_run(client)
        await settle(client, run["id"])

        events = (await client.get(f"/api/runs/{run['id']}/log")).json()

        assert events
        assert events[0]["message"] == "Run created."
        assert all(event["run_id"] == run["id"] for event in events)

    async def test_the_log_can_be_resumed_from_an_id(self, client: AsyncClient):
        run = await create_run(client)
        await settle(client, run["id"])

        everything = (await client.get(f"/api/runs/{run['id']}/log")).json()
        rest = (await client.get(f"/api/runs/{run['id']}/log?after_id={everything[0]['id']}")).json()

        assert [event["id"] for event in rest] == [event["id"] for event in everything[1:]]

    async def test_the_log_of_an_unknown_run_is_a_404(self, client: AsyncClient):
        assert (await client.get("/api/runs/nope/log")).status_code == 404


class TestEventStream:
    async def test_it_streams_and_closes_when_the_run_settles(self, client: AsyncClient):
        run = await create_run(client)

        messages, ended = await read_stream(client, run["id"])

        assert messages, "expected at least one event"
        assert any("Product Manager" in message["message"] for message in messages)
        assert ended is not None
        assert ended["status"] == RunStatus.AWAITING_PM_REVIEW

    async def test_a_late_subscriber_still_gets_the_whole_log(self, client: AsyncClient):
        """Connecting after a stage finished must not lose its output."""
        run = await create_run(client)
        await settle(client, run["id"])

        messages, _ = await read_stream(client, run["id"])
        assert messages[0]["message"] == "Run created."

    async def test_replay_can_start_from_an_offset(self, client: AsyncClient):
        run = await create_run(client)
        await settle(client, run["id"])

        everything = (await client.get(f"/api/runs/{run['id']}/log")).json()
        messages, _ = await read_stream(client, run["id"], after_id=everything[0]["id"])

        assert all(message["id"] > everything[0]["id"] for message in messages)

    async def test_streaming_an_unknown_run_is_a_404(self, client: AsyncClient):
        assert (await client.get("/api/runs/nope/events")).status_code == 404


async def read_stream(client: AsyncClient, run_id: str, after_id: int = 0):
    """Consume an SSE response to its end, returning (messages, end payload)."""
    messages: list[dict] = []
    ended: dict | None = None
    event_name = "message"

    url = f"/api/runs/{run_id}/events?after_id={after_id}"
    async with client.stream("GET", url, timeout=60.0) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                payload = json.loads(line.removeprefix("data:").strip())
                if event_name == "end":
                    ended = payload
                    break
                messages.append(payload)

    return messages, ended


# ─────────────────────────────────────────────────────────────────
# Files and downloads
# ─────────────────────────────────────────────────────────────────


class TestFiles:
    async def test_generated_files_are_listed(self, client: AsyncClient):
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        files = (await client.get(f"/api/runs/{run_id}/files")).json()["files"]
        paths = [entry["path"] for entry in files]

        assert f"source/{fakes.SERVICE_SLUG}/app/calculator.py" in paths
        assert any(entry["is_generated_test"] for entry in files)

    async def test_a_file_can_be_read_with_its_language(self, client: AsyncClient):
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        path = f"source/{fakes.SERVICE_SLUG}/app/calculator.py"
        body = (await client.get(f"/api/runs/{run_id}/files/{path}")).json()

        assert body["language"] == "python"
        assert "def add(" in body["content"]

    async def test_a_missing_file_is_a_404(self, client: AsyncClient):
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        assert (await client.get(f"/api/runs/{run_id}/files/source/nope.py")).status_code == 404

    async def test_traversal_out_of_the_workspace_is_refused(self, client: AsyncClient):
        """The path segment is attacker-controlled, so it must be contained."""
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        response = await client.get(f"/api/runs/{run_id}/files/../../../../pyproject.toml")

        assert response.status_code in (400, 404)
        assert "tool.ruff" not in response.text


class TestDownloads:
    async def test_the_pdfs_are_downloadable(self, client: AsyncClient):
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        assert "product_manager.pdf" in detail["artifacts"]

        response = await client.get(f"/api/runs/{run_id}/artifacts/product_manager.pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")

    async def test_an_unknown_artifact_is_a_404(self, client: AsyncClient):
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        assert (await client.get(f"/api/runs/{run_id}/artifacts/nope.pdf")).status_code == 404

    async def test_the_project_zip_is_downloadable(self, client: AsyncClient):
        detail = await run_to_completion(client)
        run_id = detail["run"]["id"]

        assert detail["has_zip"]

        response = await client.get(f"/api/runs/{run_id}/zip")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.content.startswith(b"PK")

    async def test_a_run_with_no_code_has_nothing_to_download(self, client: AsyncClient):
        run = await create_run(client, auto_start=False)
        assert (await client.get(f"/api/runs/{run['id']}/zip")).status_code == 404


# ─────────────────────────────────────────────────────────────────
# Startup behaviour
# ─────────────────────────────────────────────────────────────────


class TestReconciliation:
    async def test_a_run_orphaned_by_a_restart_is_failed(self, stub_llm, isolated_env):
        """A row still marked running after startup has no task behind it."""
        from server.db import open_database
        from server.models import RunRecord

        settings_path = isolated_env / "data" / "agentforge.db"

        async with open_database(settings_path) as database:
            await database.create_run(
                RunRecord(
                    id="orphan",
                    name="Interrupted",
                    requirement=REQUIREMENT,
                    status=RunStatus.RUNNING,
                )
            )

        app = create_app()
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as http:
                detail = (await http.get("/api/runs/orphan")).json()

        assert detail["run"]["status"] == RunStatus.FAILED
        assert "restart" in detail["run"]["error"]
