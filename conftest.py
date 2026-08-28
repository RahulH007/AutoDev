"""Root pytest configuration.

Puts the repository root on ``sys.path`` (the project is run as scripts, not an
installed package) and isolates every test from the developer's real ``.env`` so
no test can reach a live model or write into the real ``runs/`` directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Anything that could point a test at a real provider or real storage.
_LEAKY_VARS = (
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "CEREBRAS_API_KEY",
    "LLM_PROVIDER",
    "LLM_FALLBACK_PROVIDERS",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "RUNS_DIR",
    "DATABASE_PATH",
    "CHECKPOINT_PATH",
    "RUNNER_BACKEND",
    "AGENTFORGE_ENV_FILE",
    "LLM_TOKENS_PER_MINUTE",
    "LLM_OUTPUT_RESERVE",
    "LLM_RETRY_BACKOFF_SECONDS",
    "LLM_RETRY_MAX_DELAY_SECONDS",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point all configurable storage at a temp directory and drop real keys."""
    from core.config import reset_settings_cache
    from llm import registry
    from llm.budget import reset_budgets

    # Settings are cached, so the cache has to be dropped on both sides of the
    # test or environment changes either do not take effect or leak forwards.
    # Token budgets are process-global for the same reason the model cache is,
    # and a budget carried between tests would make one test wait on another's
    # spending -- in real seconds.
    reset_settings_cache()
    registry.reset_cache()
    reset_budgets()

    for name in _LEAKY_VARS:
        monkeypatch.delenv(name, raising=False)

    # A real .env in the repo root must never be read during tests.
    monkeypatch.setenv("AGENTFORGE_ENV_FILE", str(tmp_path / "absent.env"))

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "agentforge.db"))
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "data" / "checkpoints.db"))
    monkeypatch.setenv("RUNNER_BACKEND", "local")
    monkeypatch.setenv("VERIFY_INSTALL_DEPS", "false")
    # Effectively unlimited: no test should ever sit through a real rate-limit
    # wait. The budget's own behaviour is driven by a fake clock in test_budget.
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "100000000")
    # Retries are real code paths worth testing, but their waits are not.
    # The cap matters as well as the backoff: a 429 carries the provider's own
    # "try again in 23.5s", which overrides backoff and would be slept for real.
    monkeypatch.setenv("LLM_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("LLM_RETRY_MAX_DELAY_SECONDS", "0")

    yield tmp_path

    reset_settings_cache()
    registry.reset_cache()
    reset_budgets()


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch):
    """An installed LLM stub pre-loaded with a healthy response for every schema.

    Individual tests override one schema with ``stub.set(Schema, ...)`` to exercise
    failure paths.
    """
    from schema.architect_schema import ArchitectSchema
    from schema.developer_schema import DeveloperSchema
    from schema.product_manager_schema import ManagerSchema
    from schema.qa_schema import QASchema
    from tests import fakes

    stub = fakes.LLMStub()
    stub.set(ManagerSchema, fakes.build_prd())
    stub.set(ArchitectSchema, fakes.build_architecture())
    stub.set(DeveloperSchema, fakes.build_developer_output())
    stub.set(QASchema, fakes.build_qa_report())
    return stub.install(monkeypatch)


@pytest.fixture
def workspace(isolated_env: Path):
    from core.paths import RunWorkspace

    return RunWorkspace.create("test-run")
