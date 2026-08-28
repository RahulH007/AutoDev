"""Run metadata and the event log, on SQLite.

Two tables. ``runs`` is what the dashboard lists; ``run_events`` is the log, kept
so a browser that reconnects mid-run can replay everything it missed rather than
joining a stream already in progress.

LangGraph's own checkpoints live in a separate database file. Mixing them would
tie the pipeline's internal state to our schema.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from core.config import get_settings
from core.logging import get_logger
from server.models import EventLevel, RunEvent, RunRecord, RunStatus, utcnow

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    name          TEXT    NOT NULL,
    requirement   TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    current_stage TEXT    NOT NULL DEFAULT '',
    retry_count   INTEGER NOT NULL DEFAULT 0,
    qa_score      REAL,
    workspace     TEXT    NOT NULL DEFAULT '',
    zip_path      TEXT    NOT NULL DEFAULT '',
    error         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    finished_at   TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    stage   TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_run_events_run ON run_events (run_id, id);
CREATE INDEX IF NOT EXISTS ix_runs_created  ON runs (created_at DESC);
"""

# Columns a caller is allowed to update, so a typo cannot build broken SQL.
_UPDATABLE = frozenset(
    {
        "name",
        "status",
        "current_stage",
        "retry_count",
        "qa_score",
        "workspace",
        "zip_path",
        "error",
        "finished_at",
    }
)

# Statuses that cannot survive a process restart: nothing is driving them anymore.
_ORPHAN_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


class Database:
    """A single connection, shared and serialised by a lock.

    SQLite handles one writer at a time. Holding one connection with a lock is
    simpler than a pool and entirely sufficient for a local, single-user service.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else get_settings().database_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> Database:
        if self._conn is not None:
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row

        # WAL lets the API read while a run is writing.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

        logger.info("Database ready at %s", self.path)
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been awaited")
        return self._conn

    # ── Runs ─────────────────────────────────────────────────────

    async def create_run(self, record: RunRecord) -> RunRecord:
        async with self._lock:
            await self.connection.execute(
                """
                INSERT INTO runs (id, name, requirement, status, current_stage, retry_count,
                                  qa_score, workspace, zip_path, error,
                                  created_at, updated_at, finished_at)
                VALUES (:id, :name, :requirement, :status, :current_stage, :retry_count,
                        :qa_score, :workspace, :zip_path, :error,
                        :created_at, :updated_at, :finished_at)
                """,
                record.model_dump(mode="json"),
            )
            await self.connection.commit()

        logger.info("Created run %s", record.id)
        return record

    async def update_run(self, run_id: str, **fields: Any) -> RunRecord | None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable: {', '.join(sorted(unknown))}")
        if not fields:
            return await self.get_run(run_id)

        values = {key: _sqlite_value(value) for key, value in fields.items()}
        values["updated_at"] = utcnow()
        assignments = ", ".join(f"{key} = :{key}" for key in values)

        async with self._lock:
            await self.connection.execute(
                f"UPDATE runs SET {assignments} WHERE id = :id",  # noqa: S608 - keys are allowlisted
                {**values, "id": run_id},
            )
            await self.connection.commit()

        return await self.get_run(run_id)

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self.connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)) as cursor:
            row = await cursor.fetchone()
        return RunRecord(**dict(row)) if row else None

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunRecord]:
        async with self.connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return [RunRecord(**dict(row)) for row in rows]

    async def count_runs(self) -> int:
        async with self.connection.execute("SELECT COUNT(*) AS n FROM runs") as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def delete_run(self, run_id: str) -> bool:
        async with self._lock:
            cursor = await self.connection.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            await self.connection.commit()
        return cursor.rowcount > 0

    async def reconcile_orphans(self) -> list[str]:
        """Fail runs left mid-flight by a crash or restart.

        A row still marked ``running`` after startup has no task behind it, and
        leaving it there would show the user a run that never moves again.
        """
        statuses = [status.value for status in _ORPHAN_STATUSES]
        placeholders = ", ".join("?" for _ in statuses)

        async with self.connection.execute(
            f"SELECT id FROM runs WHERE status IN ({placeholders})",  # noqa: S608 - generated placeholders only
            statuses,
        ) as cursor:
            orphans = [row["id"] for row in await cursor.fetchall()]

        for run_id in orphans:
            await self.update_run(
                run_id,
                status=RunStatus.FAILED,
                error="Interrupted by a server restart.",
                finished_at=utcnow(),
            )
            await self.append_event(
                run_id,
                "The server restarted while this run was in progress.",
                level=EventLevel.ERROR,
            )

        if orphans:
            logger.warning("Marked %d interrupted run(s) as failed", len(orphans))
        return orphans

    # ── Events ───────────────────────────────────────────────────

    async def append_event(
        self,
        run_id: str,
        message: str,
        *,
        level: EventLevel = EventLevel.INFO,
        stage: str = "",
    ) -> RunEvent:
        event = RunEvent(run_id=run_id, level=level, stage=stage, message=message)

        async with self._lock:
            cursor = await self.connection.execute(
                """
                INSERT INTO run_events (run_id, ts, level, stage, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, event.ts, event.level.value, event.stage, event.message),
            )
            await self.connection.commit()

        event.id = cursor.lastrowid or 0
        return event

    async def list_events(self, run_id: str, after_id: int = 0, limit: int = 1000) -> list[RunEvent]:
        """Events after ``after_id``, which is how a reconnecting client catches up."""
        async with self.connection.execute(
            "SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
            (run_id, after_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [RunEvent(**dict(row)) for row in rows]


def average_quality_score(qa_report: dict[str, Any] | None) -> float | None:
    """Mean of the per-service scores, or ``None`` when QA never ran."""
    reports: Iterable[dict[str, Any]] = (qa_report or {}).get("service_reports") or []
    scores = [
        float(report["code_quality_score"])
        for report in reports
        if report.get("code_quality_score") is not None
    ]
    return round(sum(scores) / len(scores), 1) if scores else None


@asynccontextmanager
async def open_database(path: Path | str | None = None) -> AsyncIterator[Database]:
    database = await Database(path).connect()
    try:
        yield database
    finally:
        await database.close()


def _sqlite_value(value: Any) -> Any:
    """StrEnum instances must be stored as their plain string value."""
    return value.value if hasattr(value, "value") else value
