"""Central configuration.

Every tunable in the system lives here and is settable through the environment or
a ``.env`` file. Nothing else in the codebase should read ``os.environ`` directly.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_ENV_FILE = ".env"


def env_file() -> str:
    """Resolved at call time, not import time, so tests can redirect it away from
    a developer's real .env and never touch a live API key."""
    return os.getenv("AGENTFORGE_ENV_FILE", DEFAULT_ENV_FILE)


class LLMProvider(StrEnum):
    GOOGLE = "google"
    GROQ = "groq"
    OPENAI = "openai"
    OLLAMA = "ollama"
    CEREBRAS = "cerebras"


class Purpose(StrEnum):
    """What a model call is for, which decides how much model to spend on it."""

    HEAVY = "heavy"  # developer agent: long, structured, high stakes
    STRUCTURED = "structured"  # pm, architecture, qa: structured JSON
    TEXT = "text"  # client-facing prose for the PDFs
    CHEAP = "cheap"  # qa triage: high volume, low stakes


class RunnerBackend(StrEnum):
    LOCAL = "local"
    DOCKER = "docker"


class Settings(BaseSettings):
    # No env_file default on purpose. get_settings() passes one explicitly, so a
    # bare Settings() reads only the process environment. Without this, a direct
    # construction in a test would silently pick up the developer's real .env.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Provider selection ───────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.GOOGLE
    # NoDecode stops pydantic-settings from JSON-decoding the env value first,
    # which lets the validator below accept a plain comma-separated string.
    llm_fallback_providers: Annotated[list[LLMProvider], NoDecode] = Field(default_factory=list)
    llm_temperature: float = 0.2
    llm_max_retries: int = 3
    # Only transient failures are retried at all; see llm/errors.py. Each
    # retry waits this long, doubling, unless the provider named its own wait.
    llm_retry_backoff_seconds: float = 2.0
    # Ceiling on any single retry wait, including one the provider asked for.
    llm_retry_max_delay_seconds: float = 60.0
    llm_timeout_seconds: int = 300

    # ── Rate limiting ────────────────────────────────────────────
    # Providers meter tokens over a rolling minute and reject anything that
    # crosses the line. The pipeline paces itself under this ceiling rather than
    # discovering it as a failed run. Default matches Groq's free tier.
    llm_tokens_per_minute: int = 12_000
    # What to reserve for a response whose size is not knowable in advance.
    llm_output_reserve: int = 2_000

    # ── Credentials ──────────────────────────────────────────────
    google_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    cerebras_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # ── Per-purpose model overrides ──────────────────────────────
    model_heavy: str | None = None
    model_structured: str | None = None
    model_text: str | None = None
    model_cheap: str | None = None

    # ── Pipeline behaviour ───────────────────────────────────────
    max_developer_retries: int = 3
    min_quality_score: int = 7
    stage_timeout_seconds: int = 600

    # ── Code verification ────────────────────────────────────────
    runner_backend: RunnerBackend = RunnerBackend.LOCAL
    verify_install_deps: bool = True
    verify_timeout_seconds: int = 300
    verify_output_cap_bytes: int = 200_000

    # ── Storage ──────────────────────────────────────────────────
    runs_dir: Path = Path("runs")
    database_path: Path = Path("data/agentforge.db")
    checkpoint_path: Path = Path("data/checkpoints.db")

    # ── Service ──────────────────────────────────────────────────
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ── Observability ────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("llm_fallback_providers", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        """Accept ``a,b,c`` from the environment as well as a real list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    def api_key_for(self, provider: LLMProvider) -> str | None:
        return {
            LLMProvider.GOOGLE: self.google_api_key,
            LLMProvider.GROQ: self.groq_api_key,
            LLMProvider.OPENAI: self.openai_api_key,
            LLMProvider.CEREBRAS: self.cerebras_api_key,
            LLMProvider.OLLAMA: None,  # local, no key needed
        }[provider]

    def model_override_for(self, purpose: Purpose) -> str | None:
        return {
            Purpose.HEAVY: self.model_heavy,
            Purpose.STRUCTURED: self.model_structured,
            Purpose.TEXT: self.model_text,
            Purpose.CHEAP: self.model_cheap,
        }[purpose]

    def configured_providers(self) -> list[LLMProvider]:
        """The primary provider followed by any usable fallbacks, deduplicated."""
        ordered = [self.llm_provider, *self.llm_fallback_providers]
        seen: list[LLMProvider] = []
        for provider in ordered:
            if provider not in seen:
                seen.append(provider)
        return seen


def shadowed_env_keys(path: str | None = None) -> list[str]:
    """Credential names whose real environment variable overrides the ``.env`` file.

    Environment variables beat the file, which is correct but invisible: a stale
    key left in a shell profile produces a 401 that looks like a bad key in
    ``.env``. Worse, a process inherits the environment it was born with, so the
    stale value survives editing the file, restarting the server, and even
    deleting the variable. Callers surface this at startup so the cause is stated
    rather than deduced.
    """
    try:
        from dotenv import dotenv_values

        file_values = dotenv_values(path or env_file())
    except (OSError, ImportError):
        return []

    return [
        name
        for name, file_value in file_values.items()
        if name.endswith("_API_KEY")
        and file_value
        and (shell_value := os.getenv(name))
        and shell_value != file_value
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(_env_file=env_file())


def reset_settings_cache() -> None:
    """Drop the cached Settings so a test can change the environment."""
    get_settings.cache_clear()
