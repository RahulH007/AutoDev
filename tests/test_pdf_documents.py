"""PDF generation: off by default, and never able to fail a stage.

Every agent produced a client-facing PDF from JSON it had just written. That is
four extra model calls per run, each reserving a full output ceiling against the
same per-minute window the code generation needs — roughly a third of a measured
ten-minute run spent re-describing documents the console already renders from
the JSON.

So it is opt-in now. And whether it is on or off, it must never be able to lose
a stage whose expensive structured call already succeeded: the render was always
guarded, but the model call in front of it was not.
"""

from __future__ import annotations

import pytest

from agents.base import write_document
from core.config import get_settings, reset_settings_cache


class TestTheDefault:
    def test_pdfs_are_off_unless_asked_for(self):
        assert get_settings().generate_pdfs is False

    def test_the_setting_is_configurable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GENERATE_PDFS", "true")
        reset_settings_cache()
        assert get_settings().generate_pdfs is True


class TestWhenDisabled:
    async def test_no_model_call_is_made(self, workspace, monkeypatch: pytest.MonkeyPatch):
        """The saving is the call, not the file."""
        from llm import registry

        called = False

        async def fail(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("a disabled PDF must not reach the model")

        monkeypatch.setattr(registry, "allm_call", fail)

        assert await write_document("prompt", workspace, "thing.pdf") is None
        assert called is False

    async def test_nothing_is_written(self, workspace, monkeypatch: pytest.MonkeyPatch):
        from llm import registry

        monkeypatch.setattr(registry, "allm_call", _text("Some prose."))

        await write_document("prompt", workspace, "thing.pdf")

        assert not (workspace.artifacts / "thing.pdf").exists()


class TestWhenEnabled:
    async def test_the_pdf_is_produced(
        self, workspace, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        from llm import registry

        monkeypatch.setattr(registry, "allm_call", _text("A client-facing brief."))

        result = await write_document("prompt", workspace, "thing.pdf")

        assert result is not None
        assert (workspace.artifacts / "thing.pdf").is_file()


class TestFailuresNeverReachTheStage:
    """Phase 2: the model call sits inside the agent's body, so an exception here
    used to fail a stage that had already produced its artifact."""

    async def test_a_model_failure_is_swallowed(
        self, workspace, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        from llm import registry

        async def boom(*args, **kwargs):
            raise RuntimeError("rate limited")

        monkeypatch.setattr(registry, "allm_call", boom)

        assert await write_document("prompt", workspace, "thing.pdf") is None

    async def test_a_budget_refusal_is_swallowed(
        self, workspace, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        """A cosmetic document must not be the thing that ends a run."""
        from llm import registry
        from llm.budget import BudgetExceededError

        async def refused(*args, **kwargs):
            raise BudgetExceededError("a single call needs more than the budget")

        monkeypatch.setattr(registry, "allm_call", refused)

        assert await write_document("prompt", workspace, "thing.pdf") is None

    async def test_a_render_failure_is_swallowed(
        self, workspace, with_pdfs, monkeypatch: pytest.MonkeyPatch
    ):
        from agents import base
        from llm import registry

        monkeypatch.setattr(registry, "allm_call", _text("prose"))
        monkeypatch.setattr(
            base, "try_save_to_pdf", lambda *a, **k: (_ for _ in ()).throw(OSError("no fonts"))
        )

        assert await write_document("prompt", workspace, "thing.pdf") is None


def _text(value: str):
    async def call(*args, **kwargs):
        return value

    return call
