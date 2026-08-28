from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.paths import UnsafePathError
from schema.developer_schema import DeveloperSchema
from schema.qa_schema import QASchema
from server.broker import EventBroker
from server.db import open_database
from server.models import EventLevel, RunEvent, RunStatus
from server.service import RunNotFoundError, RunService, RunStateError, _derive_name
from state.state import Stage
from tests import fakes

REQUIREMENT = "Build an expense tracker with login and monthly reports."


@pytest.fixture
async def service(tmp_path: Path, stub_llm):
    async with open_database(tmp_path / "runs.db") as database:
        instance = await RunService(database).start()
        try:
            yield instance
        finally:
            await instance.stop()


async def run_to_completion(service: RunService, requirement: str = REQUIREMENT):
    """Create a run and approve both review gates."""
    record = await service.create(requirement)
    await service.begin(record.id)

    for _ in range(3):
        record = await service.wait(record.id)
        if not record.status.is_awaiting_review:
            break
        await service.approve(record.id)

    return await service.wait(record.id)


# ─────────────────────────────────────────────────────────────────
# Broker
# ─────────────────────────────────────────────────────────────────


class TestEventBroker:
    async def test_a_subscriber_receives_published_events(self):
        broker = EventBroker()
        async with broker.subscribe("run-1") as queue:
            await broker.publish(RunEvent(run_id="run-1", message="hello"))
            assert (await queue.get()).message == "hello"

    async def test_events_for_other_runs_are_not_delivered(self):
        broker = EventBroker()
        async with broker.subscribe("run-1") as queue:
            await broker.publish(RunEvent(run_id="run-2", message="not mine"))
            assert queue.empty()

    async def test_every_subscriber_gets_a_copy(self):
        broker = EventBroker()
        async with broker.subscribe("run-1") as first, broker.subscribe("run-1") as second:
            await broker.publish(RunEvent(run_id="run-1", message="broadcast"))
            assert (await first.get()).message == "broadcast"
            assert (await second.get()).message == "broadcast"

    async def test_unsubscribing_is_automatic(self):
        broker = EventBroker()
        async with broker.subscribe("run-1"):
            assert broker.subscriber_count("run-1") == 1
        assert broker.subscriber_count("run-1") == 0

    async def test_a_stalled_subscriber_cannot_block_the_pipeline(self):
        """Dropping events beats stalling a run for a browser tab nobody is reading."""
        broker = EventBroker(maxsize=2)
        async with broker.subscribe("run-1") as queue:
            for index in range(10):
                await broker.publish(RunEvent(run_id="run-1", message=str(index)))
            assert queue.qsize() == 2


# ─────────────────────────────────────────────────────────────────
# Creating runs
# ─────────────────────────────────────────────────────────────────


class TestCreate:
    async def test_it_records_the_run_and_makes_a_workspace(self, service: RunService):
        record = await service.create(REQUIREMENT)

        assert record.status is RunStatus.QUEUED
        assert record.requirement == REQUIREMENT
        assert Path(record.workspace).is_dir()
        assert (await service.get(record.id)).id == record.id

    async def test_each_run_gets_its_own_workspace(self, service: RunService):
        first = await service.create(REQUIREMENT)
        second = await service.create(REQUIREMENT)

        assert first.workspace != second.workspace

    async def test_an_empty_requirement_is_refused(self, service: RunService):
        with pytest.raises(ValueError, match="requirement"):
            await service.create("   ")

    async def test_an_explicit_name_is_kept(self, service: RunService):
        record = await service.create(REQUIREMENT, name="My Tracker")
        assert record.name == "My Tracker"

    async def test_an_unknown_run_raises(self, service: RunService):
        with pytest.raises(RunNotFoundError):
            await service.get("no-such-run")

    def test_a_long_requirement_becomes_a_short_name(self):
        name = _derive_name("a" * 200)
        assert len(name) <= 70
        assert name.endswith("…")

    def test_a_short_requirement_is_used_verbatim(self):
        assert _derive_name("Build a blog\nwith comments") == "Build a blog"


# ─────────────────────────────────────────────────────────────────
# Review gates
# ─────────────────────────────────────────────────────────────────


class TestReviewGates:
    async def test_a_run_pauses_for_the_prd(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        record = await service.wait(record.id)

        assert record.status is RunStatus.AWAITING_PM_REVIEW
        assert record.current_stage == Stage.PM.value

    async def test_the_run_is_renamed_from_the_prd(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        record = await service.wait(record.id)

        assert record.name == "SpendWise"

    async def test_approving_advances_to_the_architecture_review(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)

        await service.approve(record.id)
        record = await service.wait(record.id)

        assert record.status is RunStatus.AWAITING_ARCHITECTURE_REVIEW
        assert record.current_stage == Stage.ARCHITECTURE.value

    async def test_the_paused_state_is_read_from_the_stage_not_guessed(self, service: RunService):
        """The old code inferred this from which artifacts were populated."""
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        record = await service.wait(record.id)

        state = await service.get_graph_state(record.id)
        assert state["current_stage"] == Stage.PM.value
        assert record.status is RunStatus.AWAITING_PM_REVIEW

    async def test_feedback_sends_the_prd_back(self, service: RunService, stub_llm):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)

        await service.submit_feedback(record.id, "Add budget alerts")
        record = await service.wait(record.id)

        assert record.status is RunStatus.AWAITING_PM_REVIEW
        assert "Add budget alerts" in fakes.render_all(stub_llm.prompts)

    async def test_blank_feedback_counts_as_approval(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)

        await service.submit_feedback(record.id, "   ")
        record = await service.wait(record.id)

        assert record.status is RunStatus.AWAITING_ARCHITECTURE_REVIEW

    async def test_approving_a_run_that_is_not_paused_is_refused(self, service: RunService):
        record = await service.create(REQUIREMENT)
        with pytest.raises(RunStateError, match="not waiting"):
            await service.approve(record.id)

    async def test_a_run_cannot_be_started_twice(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)

        with pytest.raises(RunStateError, match="already been started"):
            await service.begin(record.id)


# ─────────────────────────────────────────────────────────────────
# Full runs
# ─────────────────────────────────────────────────────────────────


class TestFullRun:
    async def test_a_clean_run_completes(self, service: RunService):
        record = await run_to_completion(service)

        assert record.status is RunStatus.COMPLETED
        assert not record.error
        assert record.finished_at
        assert record.retry_count == 1

    async def test_the_quality_score_is_recorded(self, service: RunService):
        record = await run_to_completion(service)
        assert record.qa_score == 9.0

    async def test_a_finished_run_is_packaged(self, service: RunService):
        record = await run_to_completion(service)

        assert record.zip_path
        assert Path(record.zip_path).is_file()

    async def test_a_failing_pipeline_is_recorded_as_failed(
        self, service: RunService, stub_llm, monkeypatch: pytest.MonkeyPatch
    ):
        from core.config import reset_settings_cache

        monkeypatch.setenv("MAX_DEVELOPER_RETRIES", "1")
        reset_settings_cache()
        stub_llm.set(DeveloperSchema, fakes.build_developer_output(broken=True))

        record = await run_to_completion(service)

        assert record.status is RunStatus.FAILED
        assert "does not compile" in record.error
        assert not record.zip_path

    async def test_runs_are_listed_and_counted(self, service: RunService):
        await service.create(REQUIREMENT)
        await service.create("Build a blog.")

        assert await service.count() == 2
        assert len(await service.list()) == 2


# ─────────────────────────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────────────────────────


class TestEvents:
    async def test_agent_logs_are_captured_as_events(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)
        await _settle_log_pump()

        messages = [event.message for event in await service.events(record.id)]
        assert any("Product Manager starting" in message for message in messages)

    async def test_events_carry_the_stage_that_produced_them(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)
        await _settle_log_pump()

        stages = {event.stage for event in await service.events(record.id)}
        assert Stage.PM.value in stages

    async def test_lifecycle_moments_are_marked(self, service: RunService):
        record = await service.create(REQUIREMENT)
        events = await service.events(record.id)

        assert events[0].level is EventLevel.STAGE
        assert events[0].message == "Run created."

    async def test_a_live_subscriber_sees_events_as_they_happen(self, service: RunService):
        record = await service.create(REQUIREMENT)

        async with service.stream(record.id) as queue:
            await service.begin(record.id)
            await service.wait(record.id)
            await _settle_log_pump()

            received = []
            while not queue.empty():
                received.append(await queue.get())

        assert any("Product Manager" in event.message for event in received)

    async def test_events_from_other_runs_are_not_mixed_in(self, service: RunService):
        first = await service.create(REQUIREMENT)
        second = await service.create("Build a blog.")

        await service.begin(first.id)
        await service.wait(first.id)
        await _settle_log_pump()

        assert await service.events(second.id) != []
        assert all(event.run_id == second.id for event in await service.events(second.id))
        assert len(await service.events(second.id)) == 1

    async def test_a_reconnecting_client_can_replay_what_it_missed(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)
        await _settle_log_pump()

        everything = await service.events(record.id)
        assert len(everything) > 2

        later = await service.events(record.id, after_id=everything[1].id)
        assert [event.id for event in later] == [event.id for event in everything[2:]]


# ─────────────────────────────────────────────────────────────────
# Browsing generated files
# ─────────────────────────────────────────────────────────────────


class TestFiles:
    async def test_generated_sources_and_tests_are_listed(self, service: RunService):
        record = await run_to_completion(service)
        paths = [entry.path for entry in service.list_files(record.id)]

        assert f"source/{fakes.SERVICE_SLUG}/app/calculator.py" in paths
        assert any(path.startswith("tests/") for path in paths)

    async def test_test_files_are_flagged_as_such(self, service: RunService):
        record = await run_to_completion(service)
        entries = {entry.path: entry for entry in service.list_files(record.id)}

        source = entries[f"source/{fakes.SERVICE_SLUG}/app/calculator.py"]
        assert not source.is_generated_test
        assert source.size > 0

        tests = [entry for entry in entries.values() if entry.path.startswith("tests/")]
        assert all(entry.is_generated_test for entry in tests)

    async def test_the_runners_virtualenv_is_hidden(self, service: RunService):
        record = await run_to_completion(service)
        workspace = service.workspace(record.id)
        (workspace.source / fakes.SERVICE_SLUG / ".venv").mkdir(parents=True, exist_ok=True)
        (workspace.source / fakes.SERVICE_SLUG / ".venv" / "pyvenv.cfg").write_text("home = x")

        assert not any(".venv" in entry.path for entry in service.list_files(record.id))

    async def test_a_file_can_be_read_back(self, service: RunService):
        record = await run_to_completion(service)
        content = service.read_file(record.id, f"source/{fakes.SERVICE_SLUG}/app/calculator.py")

        assert "def add(" in content

    async def test_a_missing_file_raises(self, service: RunService):
        record = await run_to_completion(service)
        with pytest.raises(FileNotFoundError):
            service.read_file(record.id, "source/nope.py")

    @pytest.mark.parametrize(
        "hostile",
        ["../../../conftest.py", "..\\..\\..\\conftest.py", "source/../../../../pyproject.toml"],
    )
    async def test_traversal_out_of_the_workspace_is_refused(
        self, service: RunService, hostile: str
    ):
        """The path arrives from a URL, so it is untrusted input."""
        record = await run_to_completion(service)
        with pytest.raises((UnsafePathError, FileNotFoundError)):
            service.read_file(record.id, hostile)

    async def test_artifacts_are_reachable_by_name(self, service: RunService):
        record = await run_to_completion(service)

        assert service.artifact_path(record.id, "product_manager.pdf").is_file()
        with pytest.raises(FileNotFoundError):
            service.artifact_path(record.id, "nope.pdf")

    async def test_packaging_is_idempotent(self, service: RunService):
        record = await run_to_completion(service)

        first = await service.package(record.id)
        second = await service.package(record.id)

        assert first == second


# ─────────────────────────────────────────────────────────────────
# Cancellation and cleanup
# ─────────────────────────────────────────────────────────────────


class TestCancellation:
    async def test_a_paused_run_can_be_cancelled(self, service: RunService):
        record = await service.create(REQUIREMENT)
        await service.begin(record.id)
        await service.wait(record.id)

        cancelled = await service.cancel(record.id)

        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.finished_at

    async def test_cancelling_a_finished_run_changes_nothing(self, service: RunService):
        record = await run_to_completion(service)
        assert (await service.cancel(record.id)).status is RunStatus.COMPLETED

    async def test_deleting_removes_the_run_and_its_events(self, service: RunService):
        record = await service.create(REQUIREMENT)

        assert await service.delete(record.id)
        with pytest.raises(RunNotFoundError):
            await service.get(record.id)

    async def test_stopping_the_service_is_safe_when_idle(self, tmp_path: Path):
        async with open_database(tmp_path / "idle.db") as database:
            instance = await RunService(database).start()
            await instance.stop()
            await instance.stop()


class TestQaRetryThroughTheService:
    async def test_a_failing_review_costs_another_developer_pass(
        self, service: RunService, stub_llm
    ):
        stub_llm.set(
            QASchema,
            fakes.build_qa_report(critical_issues=1),
            fakes.build_qa_report(),
        )

        record = await run_to_completion(service)

        assert record.status is RunStatus.COMPLETED
        assert record.retry_count == 2
        assert stub_llm.calls_for(DeveloperSchema) == 2


async def _settle_log_pump() -> None:
    """Let the log pump drain before asserting on persisted events."""
    for _ in range(20):
        await asyncio.sleep(0.01)
