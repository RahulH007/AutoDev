"""Central configuration.

Every tunable in the system lives here and is settable through the environment or
a ``.env`` file. Nothing else in the codebase should read ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field as dataclass_field
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


# How many numbered keys are read for a provider that supports several accounts.
MAX_ACCOUNTS = 4


@dataclass(frozen=True)
class ProviderAccount:
    """One set of credentials for a provider, and the capacity that comes with it.

    A provider meters per account, so four Groq keys are four independent
    per-minute windows rather than one shared four times over. Everything
    downstream — the token budget, the client cache, the ledger — is keyed by
    this ``id`` so those windows stay apart.

    ``id`` is ``None`` for a provider configured the way it always was, with a
    single unnumbered key. That is what keeps every existing budget key, cache
    key and ledger record identical for anyone who has not asked for a pool.

    The key itself is ``repr=False``: this object is logged, put in error
    messages and held in caches, and a credential must never travel with it.
    """

    id: str | None
    api_key: str | None = dataclass_field(default=None, repr=False)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.id or "default"


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
    # Providers cap a completion well below what a large schema needs: Groq
    # defaults qwen/qwen3.8-27b to 2048, which truncates a ManagerSchema PRD
    # mid-JSON and fails every rung of the structured ladder. Asked for
    # explicitly so the ceiling is ours rather than the provider's surprise.
    # Keep it comfortably under llm_tokens_per_minute -- a single completion
    # larger than the whole budget can never be paced, only refused.
    # 0 or less means "use whatever the provider defaults to".
    llm_max_output_tokens: int = 4096

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

    # ── Groq capacity pool ───────────────────────────────────────
    # Groq meters tokens per organisation, so several accounts are several
    # independent per-minute windows. Numbered keys turn them into a pool the
    # registry can spread work across; each one gets its own token budget, its
    # own learned provider limit and its own line in the ledger.
    #
    # Numbered keys take precedence over the plain GROQ_API_KEY rather than
    # joining it, so a pool is exactly the accounts that were numbered and
    # nothing is silently inherited from an earlier configuration. Set none of
    # them and behaviour is unchanged in every respect.
    groq_api_key_1: str | None = None
    groq_api_key_2: str | None = None
    groq_api_key_3: str | None = None
    groq_api_key_4: str | None = None

    # ── Per-purpose model overrides ──────────────────────────────
    model_heavy: str | None = None
    model_structured: str | None = None
    model_text: str | None = None
    model_cheap: str | None = None

    # ── Difficulty-based routing ─────────────────────────────────
    # A developer service used to get the same model whether it was a three-file
    # worker or a thirty-endpoint API. When this is on, the architecture's own
    # description of a service — its files, endpoints, data models and
    # dependencies — picks a model tier, so a small service is written by a small
    # model. See llm/routing.py for the policy and its limits.
    #
    # Off by default, and off means the model resolution that has always applied.
    routing_enabled: bool = False
    # Where the two tier boundaries fall on the difficulty score. The middle band
    # is deliberately the widest: an ordinary service should be ordinary, and a
    # policy that called most work HIGH would spend more than the static
    # selection it replaced. Calibrated so a handful of files and endpoints is
    # LOW, a typical service is MEDIUM, and only a genuinely large one is HIGH.
    routing_low_max_score: int = 12
    routing_high_min_score: int = 48

    # Which model serves each tier. Separate from credentials on purpose: an
    # account says *who is paying*, a tier says *what to run*, and the two vary
    # independently — four keys can all run the same three models. Unset falls
    # back to the provider's per-purpose default, which is also what makes a tier
    # nobody configured simply unavailable rather than broken.
    groq_model_low: str | None = None
    groq_model_medium: str | None = None
    groq_model_high: str | None = None

    # How many times one service may be moved to a stronger model because
    # verification said the last one was not good enough. One by default, and one
    # is a real limit rather than a placeholder: a second upgrade doubles the
    # price of a service that has already failed twice, and if two tiers could not
    # write it the problem is more likely the brief than the model. Escalation
    # changes *which* model a call uses and never how many calls are made, so this
    # multiplies nothing — the graph's own retry budget still bounds the work.
    max_model_escalations: int = 1

    # ── Per-purpose output ceilings ──────────────────────────────
    # One global ceiling has to be set for the largest answer the pipeline ever
    # asks for, and every other call then reserves that same figure against the
    # window whether it could use it or not. QA triage returns a list of file
    # paths; holding 4,096 tokens of an 8,000 token window for it is half the
    # minute spent on nothing. So each purpose names its own, sized to what it
    # actually returns. ``None`` falls back to llm_max_output_tokens; 0 or less
    # hands the decision back to the provider, exactly as the global one does.
    max_output_heavy: int | None = 4096
    max_output_structured: int | None = 3500
    max_output_text: int | None = 2000
    max_output_cheap: int | None = 1000

    # ── Adaptive ceilings ────────────────────────────────────────
    # A per-purpose ceiling has to be sized for the largest answer that purpose
    # ever gives, and every smaller call then reserves that same figure against
    # the window. A three-file service and a thirty-file service are both HEAVY,
    # and both hold 4,096 tokens of the minute.
    #
    # When this is on, a caller that can say something deterministic about the
    # size of the answer it is asking for gets a ceiling sized to that instead --
    # never above the configured one, which stays the hard maximum. A caller with
    # nothing to say gets the configured ceiling exactly as before, so this can
    # only ever narrow, never widen or guess.
    #
    # Off by default: a ceiling set too low truncates a structured response
    # mid-JSON, which fails every rung of the ladder and costs more than the
    # window it saved.
    adaptive_ceilings: bool = False
    # The floor no adapted ceiling may go below. 1,024 rather than something
    # smaller for two reasons: it is above `max_output_cheap`, the smallest
    # ceiling this project already ships and trusts in real runs, so adapting can
    # never produce a completion budget the pipeline has not already proven; and
    # it comfortably holds a complete small JSON object for every schema in
    # `schema/`, which is what stops a narrowed ceiling truncating one. Clamped
    # against the configured ceiling, so it can never raise one.
    adaptive_min_output_tokens: int = 1024
    # Adapted ceilings are rounded up to a multiple of this. Two reasons, and the
    # second is the important one: it adds a little slack, and it stops a
    # thousand nearly-identical estimates fragmenting the model cache into a
    # thousand near-identical clients. The cache is keyed by the resolved
    # ceiling, so the ceiling has to land on few enough values to be reusable.
    adaptive_ceiling_block: int = 256

    # What one generated service is expected to cost, in output tokens. Sized
    # from what the architecture actually asks for: a fixed allowance for the
    # project-level fields of DeveloperSchema (readme, setup notes, dependency
    # files), plus an allowance per required file, plus a smaller one per
    # endpoint and data model, because a service with two files and twenty
    # endpoints writes far more than its file count suggests.
    #
    # Deliberately coarse. The result is clamped to the configured ceiling, so
    # over-estimating costs nothing at all, and only under-estimating is a
    # problem -- which is why the per-declaration allowance exists.
    adaptive_service_base_tokens: int = 800
    adaptive_service_file_tokens: int = 400
    adaptive_service_declaration_tokens: int = 150

    # ── Client-facing documents ──────────────────────────────────
    # Every agent used to turn the JSON it had just produced into prose and
    # render a PDF: four extra model calls per run, each reserving a full output
    # ceiling against the same per-minute window the code generation needs. The
    # console renders the same documents from the JSON, live and in more detail,
    # so this is opt-in until an on-demand export exists.
    generate_pdfs: bool = False

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
    # Both loopback spellings: the browser treats ``localhost`` and ``127.0.0.1``
    # as distinct origins, and the console is reachable under either one.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    # An exact-string allowlist cannot survive local development: Next.js moves the
    # console to 3001 when another dev server holds 3000, and the resulting origin
    # is rejected by a list that names a port. Matching any loopback origin instead
    # is what "allow the console on this machine" actually means. It grants nothing
    # to a remote page — evil.example can never present a loopback Origin — so the
    # reach is other local servers, which could call the API directly regardless.
    # Set false to pin deployments to ``cors_origins`` alone.
    cors_allow_loopback: bool = True

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

    def tier_model_override(self, provider: LLMProvider, tier: str) -> str | None:
        """A configured model for one provider and tier, if there is one.

        ``tier`` is the plain string value of a `llm.routing.ModelTier`; this is
        deliberately not typed against that enum so configuration keeps its
        current direction of dependency and the routing policy stays something
        `core.config` knows nothing about.

        Only Groq is tiered today. A provider with no tier models configured
        returns ``None`` for every tier, which is what makes routing quietly
        inapplicable to it rather than something to special-case.
        """
        if provider is not LLMProvider.GROQ:
            return None
        return {
            "low": self.groq_model_low,
            "medium": self.groq_model_medium,
            "high": self.groq_model_high,
        }.get(tier)

    def numbered_keys_for(self, provider: LLMProvider) -> list[tuple[int, str]]:
        """Configured numbered credentials for a pooled provider, in order.

        Only Groq is pooled today. Gaps are allowed and simply skipped: setting
        keys 1 and 3 gives a pool of two, still called ``groq-1`` and ``groq-3``,
        so an account's name does not shift when a sibling is removed.
        """
        if provider is not LLMProvider.GROQ:
            return []
        return [
            (index, key)
            for index in range(1, MAX_ACCOUNTS + 1)
            if (key := getattr(self, f"groq_api_key_{index}", None))
        ]

    def accounts_for(self, provider: LLMProvider) -> list[ProviderAccount]:
        """Every usable set of credentials for this provider, in a stable order.

        Empty when the provider has no credentials at all, which is what makes it
        unusable. Ollama runs locally and needs none, so it always has exactly one
        account.

        A provider configured the single way it always was returns one account
        whose ``id`` is ``None`` — deliberately, because that identity is what
        every budget key, cache key and ledger record already uses. Nothing about
        a single-key setup changes shape just because pooling now exists.
        """
        numbered = self.numbered_keys_for(provider)
        if numbered:
            return [
                ProviderAccount(id=f"{provider.value}-{index}", api_key=key)
                for index, key in numbered
            ]

        if provider is LLMProvider.OLLAMA:
            return [ProviderAccount(id=None)]

        key = self.api_key_for(provider)
        return [ProviderAccount(id=None, api_key=key)] if key else []

    def model_override_for(self, purpose: Purpose) -> str | None:
        return {
            Purpose.HEAVY: self.model_heavy,
            Purpose.STRUCTURED: self.model_structured,
            Purpose.TEXT: self.model_text,
            Purpose.CHEAP: self.model_cheap,
        }[purpose]

    def max_output_for(self, purpose: Purpose, demand: int | None = None) -> int:
        """The ceiling on one completion for this purpose.

        Resolved in one place because two callers need the same answer: the
        provider client is built with it, and the token budget reserves it. A
        disagreement between those two is how a request the budget waved through
        came back as a 413. That is still true with ``demand`` in the picture —
        the registry resolves once and hands the *figure* to both.

        ``demand`` is what the caller can say, deterministically and before the
        call, about how large the answer needs to be. It only ever narrows:

            configured ceiling
                   ↓
            round the demand up to a block
                   ↓
            clamp to [minimum, configured]

        Omitting it, or switching ``adaptive_ceilings`` off, returns exactly the
        configured figure — which is why a caller that knows nothing useful must
        pass nothing rather than guess.
        """
        override = {
            Purpose.HEAVY: self.max_output_heavy,
            Purpose.STRUCTURED: self.max_output_structured,
            Purpose.TEXT: self.max_output_text,
            Purpose.CHEAP: self.max_output_cheap,
        }[purpose]
        configured = self.llm_max_output_tokens if override is None else override

        # A configured ceiling of zero or less means "the provider decides", and
        # there is no figure there to narrow.
        if not self.adaptive_ceilings or configured <= 0:
            return configured
        if demand is None or demand <= 0:
            return configured

        block = max(1, self.adaptive_ceiling_block)
        target = -(-int(demand) // block) * block  # ceil to the next whole block

        # The floor is itself clamped, so a minimum set above a purpose's
        # configured ceiling raises nothing: the configured value is the maximum.
        floor = min(self.adaptive_min_output_tokens, configured)
        return max(floor, min(target, configured))

    def configured_providers(self) -> list[LLMProvider]:
        """The primary provider followed by any usable fallbacks, deduplicated."""
        ordered = [self.llm_provider, *self.llm_fallback_providers]
        seen: list[LLMProvider] = []
        for provider in ordered:
            if provider not in seen:
                seen.append(provider)
        return seen


def shadowed_env_keys(path: str | None = None) -> list[str]:
    """Setting names whose real environment variable overrides the ``.env`` file.

    Environment variables beat the file, which is correct but invisible: a stale
    key left in a shell profile produces a 401 that looks like a bad key in
    ``.env``. Worse, a process inherits the environment it was born with, so the
    stale value survives editing the file, restarting the server, and even
    deleting the variable. Callers surface this at startup so the cause is stated
    rather than deduced.

    Every key in the file is compared, not just credentials: a shadowed
    ``CORS_ORIGINS`` silently rejected the console's preflights while ``.env``
    plainly listed the right origin, and the warning that would have named the
    cause never fired because the name did not end in ``_API_KEY``. Any setting
    can be shadowed, so any setting is worth naming. Values are never logged —
    the name alone is what the reader needs, and some of these are secrets.
    """
    try:
        from dotenv import dotenv_values

        file_values = dotenv_values(path or env_file())
    except (OSError, ImportError):
        return []

    return [
        name
        for name, file_value in file_values.items()
        if file_value and (shell_value := os.getenv(name)) and shell_value != file_value
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(_env_file=env_file())


def reset_settings_cache() -> None:
    """Drop the cached Settings so a test can change the environment."""
    get_settings.cache_clear()
