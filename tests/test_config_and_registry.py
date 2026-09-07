from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from core.config import (
    LLMProvider,
    Purpose,
    Settings,
    get_settings,
    reset_settings_cache,
    shadowed_env_keys,
)
from llm import registry
from llm.budget import BudgetExceededError, budget_for, reset_budgets


class TestSettings:
    def test_csv_environment_values_become_lists(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "groq, openai")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
        reset_settings_cache()

        settings = get_settings()
        assert settings.llm_fallback_providers == [LLMProvider.GROQ, LLMProvider.OPENAI]
        assert settings.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]

    def test_configured_providers_puts_primary_first_and_deduplicates(self):
        settings = Settings(
            llm_provider=LLMProvider.GROQ,
            llm_fallback_providers=[LLMProvider.GOOGLE, LLMProvider.GROQ],
        )
        assert settings.configured_providers() == [LLMProvider.GROQ, LLMProvider.GOOGLE]

    def test_api_key_lookup_is_per_provider(self):
        settings = Settings(google_api_key="g", groq_api_key="q")
        assert settings.api_key_for(LLMProvider.GOOGLE) == "g"
        assert settings.api_key_for(LLMProvider.GROQ) == "q"
        assert settings.api_key_for(LLMProvider.OPENAI) is None
        # Ollama runs locally and needs no credential.
        assert settings.api_key_for(LLMProvider.OLLAMA) is None

    def test_log_level_is_normalised(self):
        assert Settings(log_level="debug").log_level == "DEBUG"

    def test_defaults_do_not_require_any_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        reset_settings_cache()
        settings = get_settings()
        assert settings.llm_provider is LLMProvider.GOOGLE
        assert settings.max_developer_retries == 3
        assert settings.min_quality_score == 7


class TestModelSelection:
    def test_purpose_picks_the_provider_default(self):
        settings = Settings()
        assert registry.model_name_for(LLMProvider.GOOGLE, Purpose.HEAVY, settings).startswith("gemini")
        assert registry.model_name_for(LLMProvider.GROQ, Purpose.CHEAP, settings) == "openai/gpt-oss-20b"

    def test_override_wins_for_that_purpose_only(self):
        settings = Settings(model_heavy="my-big-model")
        assert registry.model_name_for(LLMProvider.GOOGLE, Purpose.HEAVY, settings) == "my-big-model"
        assert registry.model_name_for(LLMProvider.GOOGLE, Purpose.CHEAP, settings) != "my-big-model"

    def test_every_provider_covers_every_purpose(self):
        for provider in LLMProvider:
            for purpose in Purpose:
                assert registry.PROVIDER_DEFAULT_MODELS[provider][purpose]


class TestProviderAvailability:
    def test_provider_without_credentials_is_not_usable(self):
        settings = Settings(llm_provider=LLMProvider.GOOGLE, google_api_key=None)
        assert registry.usable_providers(settings) == []

    def test_fallbacks_without_keys_are_dropped(self):
        settings = Settings(
            llm_provider=LLMProvider.GOOGLE,
            google_api_key="present",
            llm_fallback_providers=[LLMProvider.GROQ, LLMProvider.OPENAI],
            groq_api_key="present",
        )
        assert registry.usable_providers(settings) == [LLMProvider.GOOGLE, LLMProvider.GROQ]

    def test_ollama_is_usable_without_a_key(self):
        settings = Settings(llm_provider=LLMProvider.OLLAMA)
        assert registry.usable_providers(settings) == [LLMProvider.OLLAMA]

    def test_missing_credentials_raise_an_actionable_error(self):
        settings = Settings(llm_provider=LLMProvider.GOOGLE, google_api_key=None)
        with pytest.raises(registry.LLMUnavailableError) as exc:
            registry.get_text_llm(Purpose.TEXT, settings)
        assert "GOOGLE_API_KEY" in str(exc.value)


class TestContentFlattening:
    def test_plain_string_passes_through(self):
        assert registry.flatten_content("hello") == "hello"

    def test_multimodal_block_list_is_joined(self):
        blocks = [{"text": "first"}, {"text": "second"}, {"type": "image"}]
        assert registry.flatten_content(blocks) == "first\nsecond"

    def test_unexpected_shapes_degrade_to_string(self):
        assert registry.flatten_content(42) == "42"


def test_importing_agents_does_not_require_an_api_key(monkeypatch: pytest.MonkeyPatch):
    """The old code built the model at import time, so a missing key was an ImportError."""
    import importlib

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    reset_settings_cache()
    registry.reset_cache()

    for module in ("agents.pm_agent", "agents.architecture_agent", "agents.developer_agent", "agents.qa_agent"):
        importlib.reload(importlib.import_module(module))


class MeteredFakeModel(Runnable):
    """A chat model that reports token usage the way a real provider does."""

    def __init__(self, total_tokens: int | None = None) -> None:
        self.total_tokens = total_tokens
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        usage = (
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": self.total_tokens}
            if self.total_tokens is not None
            else None
        )
        return AIMessage(content="ok", usage_metadata=usage)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        return self.invoke(input, config)


class TestTokenBudgetWiring:
    """The budget has to sit on the real call path, not just exist beside it."""

    @staticmethod
    def _budget_for_text(settings: Settings | None = None):
        settings = settings or get_settings()
        model = registry.model_name_for(LLMProvider.GOOGLE, Purpose.TEXT, settings)
        return budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)

    def test_a_text_call_charges_the_shared_budget(self, monkeypatch: pytest.MonkeyPatch):
        reset_budgets()
        model = MeteredFakeModel(total_tokens=777)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.TEXT)

        assert self._budget_for_text().used() == 777

    def test_a_response_without_usage_metadata_keeps_the_estimate(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.TEXT)

        # Nothing to reconcile against, so the reservation stands rather than
        # silently freeing budget the call actually spent.
        assert self._budget_for_text().used() > 0

    def test_a_call_too_large_for_the_whole_budget_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "10")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=5)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(BudgetExceededError):
            registry.llm_call("hello there", Purpose.TEXT)

        assert model.calls == 0

    def test_every_purpose_on_one_model_shares_a_single_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Groq meters per organisation; separate budgets would overcommit it."""
        reset_budgets()
        monkeypatch.setenv("MODEL_TEXT", "shared-model")
        monkeypatch.setenv("MODEL_CHEAP", "shared-model")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=100)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("first", Purpose.TEXT)
        registry.llm_call("second", Purpose.CHEAP)

        budget = budget_for(LLMProvider.GOOGLE, "shared-model", get_settings().llm_tokens_per_minute)
        assert budget.used() == 200


# ── Error classification on the real call path ────────────────────

TOOL_CHOICE_400 = (
    "Error code: 400 - {'error': {'message': 'Tool choice is required, but model "
    "did not call a tool', 'code': 'tool_use_failed'}}"
)
UPSTREAM_503 = "Error code: 503 - {'error': {'message': 'Service Unavailable'}}"


class FlakyFakeModel(Runnable):
    """Raises the given errors in order, then answers."""

    def __init__(self, *errors: Exception, always: Exception | None = None, content: str = "ok"):
        self.errors = list(errors)
        self.always = always
        self.content = content
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        if self.always is not None:
            raise self.always
        if self.errors:
            raise self.errors.pop(0)
        return AIMessage(content=self.content)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        return self.invoke(input, config)


class TestRetryPolicy:
    """A failed call is repeated only when repeating it could plausibly help."""

    def test_a_deterministic_failure_is_attempted_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Three identical 400s taught the last run nothing and cost it the budget."""
        model = FlakyFakeModel(always=Exception(TOOL_CHOICE_400))
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(Exception, match="Tool choice is required"):
            registry.llm_call("hello", Purpose.TEXT)

        assert model.calls == 1

    def test_a_transient_failure_is_still_retried(self, monkeypatch: pytest.MonkeyPatch):
        model = FlakyFakeModel(Exception(UPSTREAM_503))
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        assert registry.llm_call("hello", Purpose.TEXT) == "ok"
        assert model.calls == 2

    def test_a_deterministic_failure_still_falls_through_to_the_next_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
        reset_settings_cache()
        primary = FlakyFakeModel(always=Exception(TOOL_CHOICE_400))
        fallback = FlakyFakeModel(content="from the fallback")
        models = {LLMProvider.GOOGLE: primary, LLMProvider.GROQ: fallback}
        # `*_` absorbs the registry's optional arguments — ceiling, account,
        # whatever comes next. Only the provider matters to this test.
        monkeypatch.setattr(
            registry, "get_chat_model", lambda purpose, provider, *_, **__: models[provider]
        )

        assert registry.llm_call("hello", Purpose.TEXT) == "from the fallback"
        assert primary.calls == 1


class LadderFakeModel(Runnable):
    """A model that can build every structured-output rung.

    Records which rungs were invoked, in order, so a test can assert the ladder
    walked rather than repeated.
    """

    METHODS = {
        "function_calling": "native",
        "json_schema": "json_schema",
        "json_mode": "json_mode",
    }

    def __init__(
        self,
        errors: dict[str, Exception] | None = None,
        flaky: dict[str, Exception] | None = None,
        content: str = '{"x": 1}',
    ) -> None:
        self.errors = errors or {}  # raised every time this rung is invoked
        self.flaky = flaky or {}  # raised once, then the rung answers
        self.content = content
        self.calls: list[str] = []

    def _respond(self, rung: str) -> AIMessage:
        self.calls.append(rung)
        if rung in self.flaky:
            raise self.flaky.pop(rung)
        if rung in self.errors:
            raise self.errors[rung]
        return AIMessage(content=self.content)

    def with_structured_output(
        self, schema: Any, *, method: str = "function_calling", **kwargs: Any
    ) -> Runnable:
        rung = self.METHODS[method]
        return RunnableLambda(
            lambda _prompt: schema.model_validate(json.loads(self._respond(rung).content))
        )

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        return self._respond("parse")

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        return self._respond("parse")


class Point(BaseModel):
    x: int


class TestStructuredLadder:
    def test_a_deterministic_failure_walks_the_ladder_without_repeating_a_rung(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        model = LadderFakeModel(
            errors={
                "native": Exception(TOOL_CHOICE_400),
                "json_schema": Exception(TOOL_CHOICE_400),
                "json_mode": Exception(TOOL_CHOICE_400),
            }
        )
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        assert registry.get_structured_llm(Point).invoke("hi") == Point(x=1)
        assert model.calls == ["native", "json_schema", "json_mode", "parse"]

    def test_a_transient_failure_repeats_the_same_rung(self, monkeypatch: pytest.MonkeyPatch):
        model = LadderFakeModel(flaky={"native": Exception(UPSTREAM_503)})
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        assert registry.get_structured_llm(Point).invoke("hi") == Point(x=1)
        # Retried in place and succeeded, rather than giving up the best rung.
        assert model.calls == ["native", "native"]

    def test_a_budget_refusal_stops_the_ladder(self, monkeypatch: pytest.MonkeyPatch):
        """Every lower rung sends a longer prompt, so falling through cannot help."""
        model = LadderFakeModel(errors={"native": BudgetExceededError("too big")})
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(BudgetExceededError):
            registry.get_structured_llm(Point).invoke("hi")

        assert model.calls == ["native"]

    def test_the_error_reported_is_the_last_rungs_not_the_first(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """with_fallbacks re-raised the first error, which pointed at the wrong cause."""
        model = LadderFakeModel(
            errors={
                "native": Exception(TOOL_CHOICE_400),
                "json_schema": Exception("Error code: 400 - json_schema unsupported"),
                "json_mode": Exception("Error code: 400 - json_mode unsupported"),
                "parse": Exception("Error code: 400 - the last word"),
            }
        )
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(Exception, match="the last word"):
            registry.get_structured_llm(Point).invoke("hi")


REAL_TPM_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "openai/gpt-oss-120b on tokens per minute (TPM): Limit 8000, Used 5276, "
    "Requested 5857. Please try again in 23.4975s.', 'code': 'rate_limit_exceeded'}}"
)


class TestBudgetLearnsFromRejections:
    """A ceiling the provider disagrees with cannot be counted around."""

    @staticmethod
    def _text_budget():
        settings = get_settings()
        model = registry.model_name_for(LLMProvider.GOOGLE, Purpose.TEXT, settings)
        return budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)

    def test_a_rate_limit_teaches_the_budget_the_real_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "12000")
        reset_settings_cache()
        model = FlakyFakeModel(always=Exception(REAL_TPM_429))
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(Exception, match="Rate limit reached"):
            registry.llm_call("hello", Purpose.TEXT)

        # Configured 12000, enforced 8000. The rejection said so.
        assert self._text_budget().limit == 8_000

    def test_a_rate_limit_resyncs_the_window_to_the_reported_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "12000")
        reset_settings_cache()
        model = FlakyFakeModel(Exception(REAL_TPM_429), content="second time lucky")
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        assert registry.llm_call("hello", Purpose.TEXT) == "second time lucky"
        assert model.calls == 2

    def test_an_ordinary_failure_leaves_the_ceiling_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "12000")
        reset_settings_cache()
        model = FlakyFakeModel(always=Exception(TOOL_CHOICE_400))
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(Exception, match="Tool choice"):
            registry.llm_call("hello", Purpose.TEXT)

        assert self._text_budget().limit == 12_000


class TestOutputTokenCeiling:
    """Providers cap completions far below what a large schema needs.

    Groq defaults `qwen/qwen3.8-27b` to 2048 completion tokens. A ManagerSchema
    PRD does not fit, so the JSON came back truncated and every rung of the
    structured ladder failed on it — the last one with
    "Expecting ',' delimiter". Nothing retries its way out of that, so the
    ceiling has to be asked for explicitly.
    """

    def test_there_is_a_configurable_ceiling(self):
        assert Settings().llm_max_output_tokens > 2048

    def test_it_is_settable_from_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "9001")
        reset_settings_cache()
        assert get_settings().llm_max_output_tokens == 9001

    def test_groq_clients_are_built_with_it(self):
        settings = Settings(groq_api_key="test-key", llm_max_output_tokens=4096)
        model = registry._build(LLMProvider.GROQ, "qwen/qwen3.8-27b", settings)
        assert model.max_tokens == 4096

    def test_openai_clients_are_built_with_it(self):
        settings = Settings(openai_api_key="test-key", llm_max_output_tokens=4096)
        model = registry._build(LLMProvider.OPENAI, "gpt-4.1-mini", settings)
        assert model.max_tokens == 4096

    def test_google_uses_its_own_parameter_name(self):
        """Gemini calls it max_output_tokens; a wrong name is silently ignored."""
        settings = Settings(google_api_key="test-key", llm_max_output_tokens=4096)
        model = registry._build(LLMProvider.GOOGLE, "gemini-2.5-flash", settings)
        assert model.max_output_tokens == 4096

    def test_the_ceiling_is_part_of_the_model_cache_key(self):
        """Same reason temperature is: it changes the client that gets built.

        ``max_output_structured=None`` defers to the global ceiling, which is the
        knob under test here; see test_output_ceilings.py for the per-purpose one.
        """
        registry.reset_cache()
        small = Settings(google_api_key="k", llm_max_output_tokens=1024, max_output_structured=None)
        large = Settings(google_api_key="k", llm_max_output_tokens=8192, max_output_structured=None)

        first = registry.get_chat_model(Purpose.STRUCTURED, LLMProvider.GOOGLE, small)
        second = registry.get_chat_model(Purpose.STRUCTURED, LLMProvider.GOOGLE, large)

        assert first is not second
        assert second.max_output_tokens == 8192

    def test_a_zero_or_negative_ceiling_means_the_provider_default(self):
        """An explicit 0 opts out rather than asking for a zero-length answer."""
        settings = Settings(groq_api_key="test-key", llm_max_output_tokens=0)
        model = registry._build(LLMProvider.GROQ, "qwen/qwen3.8-27b", settings)
        assert model.max_tokens is None


TOO_LARGE_413 = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "qwen/qwen3.8-27b on tokens per minute (TPM): Limit 8000, Requested 9200, "
    "please reduce your message size and try again.'}}"
)


class TestReservationCoversTheOutputCeiling:
    """The budget must refuse what the provider would refuse.

    Providers charge `prompt + max_completion_tokens` against the per-minute
    window. Reserving only `llm_output_reserve` under-counts whenever the output
    ceiling is the larger of the two, so the budget waves through a request the
    provider answers with 413 — which is exactly what happened at the
    architecture stage of run 9caeb0c9: Limit 8000, Requested 9200.

    The ceiling is now resolved per purpose, so each case sets `MAX_OUTPUT_TEXT`
    alongside the global one: the reservation has to cover whatever the client
    was actually built with. How that figure is resolved is test_output_ceilings.py.
    """

    @staticmethod
    def _budget_for_text():
        settings = get_settings()
        model = registry.model_name_for(LLMProvider.GOOGLE, Purpose.TEXT, settings)
        return budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute)

    def test_a_request_the_provider_would_reject_is_refused_locally(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Tiny prompt, but prompt + ceiling exceeds the window."""
        reset_budgets()
        monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "4000")
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "2000")
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
        monkeypatch.setenv("MAX_OUTPUT_TEXT", "4096")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=5)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(BudgetExceededError):
            registry.llm_call("hello there", Purpose.TEXT)

        # Refused before the request left the process, which is the whole point.
        assert model.calls == 0

    def test_the_reservation_uses_the_ceiling_when_it_is_larger(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "2000")
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
        monkeypatch.setenv("MAX_OUTPUT_TEXT", "4096")
        reset_settings_cache()
        # No usage metadata, so the reservation stands and can be measured.
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.TEXT)

        assert self._budget_for_text().used() >= 4096

    def test_the_reserve_still_wins_when_it_is_the_larger_of_the_two(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        reset_budgets()
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "3000")
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "1000")
        monkeypatch.setenv("MAX_OUTPUT_TEXT", "1000")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.TEXT)

        assert self._budget_for_text().used() >= 3000

    def test_opting_out_of_the_ceiling_falls_back_to_the_reserve(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """0 means 'provider default', which we cannot know, so reserve as before."""
        reset_budgets()
        monkeypatch.setenv("LLM_OUTPUT_RESERVE", "2000")
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "0")
        monkeypatch.setenv("MAX_OUTPUT_TEXT", "0")
        reset_settings_cache()
        model = MeteredFakeModel(total_tokens=None)
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        registry.llm_call("hello there", Purpose.TEXT)

        used = self._budget_for_text().used()
        assert 2000 <= used < 3000


class TestOversizedRequestsDoNotWalkTheLadder:
    def test_a_413_stops_the_ladder_rather_than_degrading(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Lower rungs append the schema, so degrading grows the request."""
        model = LadderFakeModel(errors={"native": Exception(TOO_LARGE_413)})
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        with pytest.raises(Exception, match="Request too large"):
            registry.get_structured_llm(Point).invoke("hi")

        assert model.calls == ["native"]

    def test_a_429_still_walks_and_retries_as_before(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The 413 change must not disturb rate-limit handling."""
        rate_limited = Exception(
            "Error code: 429 - Rate limit reached on tokens per minute (TPM): "
            "Limit 8000, Used 3912, Requested 4496. Please try again in 3.06s."
        )
        model = LadderFakeModel(flaky={"native": rate_limited})
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)

        assert registry.get_structured_llm(Point).invoke("hi") == Point(x=1)
        assert model.calls == ["native", "native"]


class TestShadowedEnvKeys:
    """Any setting can be shadowed by the shell, so any setting must be named.

    A shell ``CORS_ORIGINS`` overrode a correct ``.env`` and rejected every
    preflight; the warning stayed silent because the check only looked at names
    ending in ``_API_KEY``.
    """

    def test_reports_a_shadowed_non_credential_setting(self, tmp_path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("CORS_ORIGINS=http://localhost:3000\n", encoding="utf-8")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:9999")

        assert shadowed_env_keys(str(env)) == ["CORS_ORIGINS"]

    def test_reports_a_shadowed_credential(self, tmp_path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("GOOGLE_API_KEY=from-file\n", encoding="utf-8")
        monkeypatch.setenv("GOOGLE_API_KEY", "from-shell")

        assert shadowed_env_keys(str(env)) == ["GOOGLE_API_KEY"]

    def test_silent_when_the_shell_agrees_with_the_file(self, tmp_path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("CORS_ORIGINS=http://localhost:3000\n", encoding="utf-8")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")

        assert shadowed_env_keys(str(env)) == []

    def test_silent_when_the_setting_is_only_in_the_file(self, tmp_path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("CORS_ORIGINS=http://localhost:3000\n", encoding="utf-8")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)

        assert shadowed_env_keys(str(env)) == []
