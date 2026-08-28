"""Where LangGraph keeps its state between steps.

Runs pause for human approval, sometimes for a long time. With the in-memory
saver a restart during that pause loses the run entirely, so the real
checkpointer is SQLite-backed and lives in its own database file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def open_checkpointer(path: Path | str | None = None) -> AsyncIterator[object]:
    """Yield a SQLite checkpointer, creating the database if it is not there."""
    target = Path(path) if path is not None else get_settings().checkpoint_path
    target.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(target)) as saver:
        logger.info("Checkpointer ready at %s", target)
        yield saver


@asynccontextmanager
async def open_memory_checkpointer() -> AsyncIterator[object]:
    """An ephemeral checkpointer, for tests and one-shot CLI runs."""
    yield MemorySaver()
