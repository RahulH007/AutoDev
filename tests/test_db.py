from __future__ import annotations

from pathlib import Path

import pytest

from server.db import Database, average_quality_score, open_database
from server.models import EventLevel, RunRecord, RunStatus


@pytest.fixture
async def db(tmp_path: Path):
    async with open_database(tmp_path / "test.db") as database:
        yield database


def make_run(run_id: str = "run-1", **overrides) -> RunRecord:
    defaults = {
        "id": run_id,
        "name": "Expense tracker",
        "requirement": "Build an expense tracker.",
        "workspace": f"runs/{run_id}",
    }
    return RunRecord(**{**defaults, **overrides})


class TestRuns:
    async def test_a_created_run_can_be_read_back(self, db: Database):
        created = await db.create_run(make_run())
        fetched = await db.get_run(created.id)

        assert fetched is not None
        assert fetched.name == "Expense tracker"
        assert fetched.status is RunStatus.QUEUED
        assert fetched.qa_score is None

    async def test_an_unknown_run_is_none_rather_than_an_error(self, db: Database):
        assert await db.get_run("nope") is None

    async def test_updates_are_applied_and_stamped(self, db: Database):
        created = await db.create_run(make_run())
        original = created.updated_at

        updated = await db.update_run(
            created.id,
            status=RunStatus.RUNNING,
            current_stage="developer_agent",
            retry_count=2,
            qa_score=8.5,
        )

        assert updated is not None
        assert updated.status is RunStatus.RUNNING
        assert updated.current_stage == "developer_agent"
        assert updated.retry_count == 2
        assert updated.qa_score == 8.5
        assert updated.updated_at >= original

    async def test_an_enum_is_stored_as_its_plain_value(self, db: Database):
        created = await db.create_run(make_run())
        await db.update_run(created.id, status=RunStatus.COMPLETED)

        async with db.connection.execute(
            "SELECT status FROM runs WHERE id = ?", (created.id,)
        ) as cursor:
            row = await cursor.fetchone()

        assert row["status"] == "completed"

    async def test_an_unknown_column_is_refused(self, db: Database):
        created = await db.create_run(make_run())
        with pytest.raises(ValueError, match="Not updatable"):
            await db.update_run(created.id, requirement="rewritten")

    async def test_runs_are_listed_newest_first(self, db: Database):
        for index in range(3):
            await db.create_run(make_run(f"run-{index}", created_at=f"2026-01-0{index + 1}T00:00:00"))

        listed = await db.list_runs()
        assert [run.id for run in listed] == ["run-2", "run-1", "run-0"]
        assert await db.count_runs() == 3

    async def test_listing_can_be_paged(self, db: Database):
        for index in range(5):
            await db.create_run(make_run(f"run-{index}", created_at=f"2026-01-0{index + 1}T00:00:00"))

        assert [run.id for run in await db.list_runs(limit=2)] == ["run-4", "run-3"]
        assert [run.id for run in await db.list_runs(limit=2, offset=2)] == ["run-2", "run-1"]

    async def test_deleting_a_run_takes_its_events_with_it(self, db: Database):
        created = await db.create_run(make_run())
        await db.append_event(created.id, "something happened")

        assert await db.delete_run(created.id)
        assert await db.get_run(created.id) is None
        assert await db.list_events(created.id) == []

    async def test_deleting_something_absent_reports_false(self, db: Database):
        assert not await db.delete_run("nope")


class TestEvents:
    async def test_events_come_back_in_order_with_ids(self, db: Database):
        created = await db.create_run(make_run())
        await db.append_event(created.id, "first")
        await db.append_event(created.id, "second", level=EventLevel.WARNING, stage="qa_agent")

        events = await db.list_events(created.id)

        assert [event.message for event in events] == ["first", "second"]
        assert events[0].id < events[1].id
        assert events[1].level is EventLevel.WARNING
        assert events[1].stage == "qa_agent"

    async def test_a_reconnecting_client_can_resume_from_an_id(self, db: Database):
        created = await db.create_run(make_run())
        first = await db.append_event(created.id, "before")
        await db.append_event(created.id, "after")

        missed = await db.list_events(created.id, after_id=first.id)
        assert [event.message for event in missed] == ["after"]

    async def test_events_are_scoped_to_their_run(self, db: Database):
        a = await db.create_run(make_run("run-a"))
        b = await db.create_run(make_run("run-b"))
        await db.append_event(a.id, "for a")
        await db.append_event(b.id, "for b")

        assert [event.message for event in await db.list_events(a.id)] == ["for a"]

    async def test_an_event_for_an_unknown_run_is_rejected(self, db: Database):
        """The foreign key is what keeps the log from filling with orphans."""
        import aiosqlite

        with pytest.raises(aiosqlite.IntegrityError):
            await db.append_event("no-such-run", "orphan")


class TestReconciliation:
    async def test_runs_left_mid_flight_are_failed_on_startup(self, db: Database):
        await db.create_run(make_run("running", status=RunStatus.RUNNING))
        await db.create_run(make_run("queued", status=RunStatus.QUEUED))
        await db.create_run(make_run("done", status=RunStatus.COMPLETED))

        orphans = await db.reconcile_orphans()

        assert sorted(orphans) == ["queued", "running"]
        for run_id in orphans:
            record = await db.get_run(run_id)
            assert record.status is RunStatus.FAILED
            assert "restart" in record.error
            assert record.finished_at

        assert (await db.get_run("done")).status is RunStatus.COMPLETED

    async def test_a_paused_run_survives_a_restart(self, db: Database):
        """Awaiting review is a legitimate resting state, not an orphan."""
        await db.create_run(make_run("paused", status=RunStatus.AWAITING_PM_REVIEW))

        assert await db.reconcile_orphans() == []
        assert (await db.get_run("paused")).status is RunStatus.AWAITING_PM_REVIEW

    async def test_reconciliation_leaves_an_explanation_in_the_log(self, db: Database):
        await db.create_run(make_run("running", status=RunStatus.RUNNING))
        await db.reconcile_orphans()

        events = await db.list_events("running")
        assert events[-1].level is EventLevel.ERROR
        assert "restarted" in events[-1].message


class TestPersistence:
    async def test_data_survives_reopening_the_file(self, tmp_path: Path):
        path = tmp_path / "persist.db"

        async with open_database(path) as database:
            await database.create_run(make_run("kept"))
            await database.append_event("kept", "still here")

        async with open_database(path) as database:
            assert (await database.get_run("kept")) is not None
            assert [event.message for event in await database.list_events("kept")] == ["still here"]

    async def test_using_a_closed_database_says_so(self, tmp_path: Path):
        database = Database(tmp_path / "closed.db")
        with pytest.raises(RuntimeError, match="connect"):
            _ = database.connection

    async def test_connecting_twice_is_harmless(self, tmp_path: Path):
        database = Database(tmp_path / "twice.db")
        await database.connect()
        await database.connect()
        await database.close()


class TestQualityScore:
    def test_it_averages_the_service_scores(self):
        report = {"service_reports": [{"code_quality_score": 8}, {"code_quality_score": 9}]}
        assert average_quality_score(report) == 8.5

    def test_no_qa_report_means_no_score(self):
        assert average_quality_score(None) is None
        assert average_quality_score({}) is None
        assert average_quality_score({"service_reports": []}) is None

    def test_services_without_a_score_are_ignored(self):
        report = {"service_reports": [{"code_quality_score": 7}, {"service_name": "x"}]}
        assert average_quality_score(report) == 7.0


class TestRunStatus:
    def test_terminal_states_are_identified(self):
        assert RunStatus.COMPLETED.is_terminal
        assert RunStatus.FAILED.is_terminal
        assert not RunStatus.RUNNING.is_terminal
        assert not RunStatus.AWAITING_PM_REVIEW.is_terminal

    def test_review_states_are_identified(self):
        assert RunStatus.AWAITING_PM_REVIEW.is_awaiting_review
        assert RunStatus.AWAITING_ARCHITECTURE_REVIEW.is_awaiting_review
        assert not RunStatus.RUNNING.is_awaiting_review

    def test_every_status_has_a_label(self):
        for status in RunStatus:
            assert status.label
