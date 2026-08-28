"""LLM provider registry.

Chat models are built lazily and cached, so importing an agent module never
requires an API key. Each call site declares a :class:`Purpose` rather than a
model name, which lets the expensive developer pass and the cheap QA triage pass
use different models without any agent knowing which provider is configured.

Retries and provider fallbacks are hand-rolled rather than taken from
LangChain's combinators, because both decisions turn on *which* error came
back: see :mod:`llm.errors`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from core.config import LLMProvider, Purpose, Settings, get_settings
from core.logging import get_logger
from llm.budget import Reservation, TokenBudget, budget_for, estimate_tokens
from llm.content import flatten_content
from llm.errors import Disposition, Retrier, classify, reported_usage
from llm.structured import as_messages, build_strategies

logger = get_logger(__name__)

# Conservative defaults. Every entry is overridable with MODEL_HEAVY,
# MODEL_STRUCTURED, MODEL_TEXT or MODEL_CHEAP.
PROVIDER_DEFAULT_MODELS: dict[LLMProvider, dict[Purpose, str]] = {
    LLMProvider.GOOGLE: {
        Purpose.HEAVY: "gemini-2.5-flash",
        Purpose.STRUCTURED: "gemini-2.5-flash",
        Purpose.TEXT: "gemini-2.5-flash",
        Purpose.CHEAP: "gemini-2.5-flash",
    },
    # Groq retires models faster than anyone else here: the Llama 3.x line these
    # used to name now returns 404 on a current account. Verify with
    # scripts/check_providers.py before assuming a name still resolves.
    LLMProvider.GROQ: {
        Purpose.HEAVY: "openai/gpt-oss-120b",
        Purpose.STRUCTURED: "openai/gpt-oss-120b",
        Purpose.TEXT: "openai/gpt-oss-20b",
        Purpose.CHEAP: "openai/gpt-oss-20b",
    },
    LLMProvider.OPENAI: {
        Purpose.HEAVY: "gpt-4.1",
        Purpose.STRUCTURED: "gpt-4.1-mini",
        Purpose.TEXT: "gpt-4.1-mini",
        Purpose.CHEAP: "gpt-4.1-nano",
    },
    LLMProvider.OLLAMA: {
        Purpose.HEAVY: "llama3.1",
        Purpose.STRUCTURED: "llama3.1",
        Purpose.TEXT: "llama3.1",
        Purpose.CHEAP: "llama3.1",
    },
    LLMProvider.CEREBRAS: {
        Purpose.HEAVY: "qwen-3-235b-a22b-instruct-2507",
        Purpose.STRUCTURED: "qwen-3-235b-a22b-instruct-2507",
        Purpose.TEXT: "qwen-3-235b-a22b-instruct-2507",
        Purpose.CHEAP: "qwen-3-235b-a22b-instruct-2507",
    },
}


class LLMUnavailableError(RuntimeError):
    """No provider is usable, usually a missing API key."""


_model_cache: dict[tuple[LLMProvider, str, float], BaseChatModel] = {}


def reset_cache() -> None:
    """Drop cached chat models. Used by tests after changing configuration."""
    _model_cache.clear()


def model_name_for(provider: LLMProvider, purpose: Purpose, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return settings.model_override_for(purpose) or PROVIDER_DEFAULT_MODELS[provider][purpose]


def _build(provider: LLMProvider, model: str, settings: Settings) -> BaseChatModel:
    """Construct a provider client. Imports are local so an uninstalled optional
    provider cannot break importing this module."""
    key = settings.api_key_for(provider)
    temperature = settings.llm_temperature
    timeout = settings.llm_timeout_seconds

    if provider is LLMProvider.GOOGLE:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=key,
            temperature=temperature,
            timeout=timeout,
            max_retries=0,  # retries are handled by the runnable wrapper
        )

    if provider is LLMProvider.GROQ:
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=key, temperature=temperature, timeout=timeout, max_retries=0)

    if provider is LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=key, temperature=temperature, timeout=timeout, max_retries=0)

    if provider is LLMProvider.CEREBRAS:
        from langchain_cerebras import ChatCerebras

        return ChatCerebras(model=model, api_key=key, temperature=temperature, timeout=timeout)

    if provider is LLMProvider.OLLAMA:
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model, base_url=settings.ollama_base_url, temperature=temperature)

    raise LLMUnavailableError(f"Unsupported provider: {provider}")


def get_chat_model(purpose: Purpose, provider: LLMProvider, settings: Settings | None = None) -> BaseChatModel:
    settings = settings or get_settings()
    model = model_name_for(provider, purpose, settings)
    cache_key = (provider, model, settings.llm_temperature)

    if cache_key not in _model_cache:
        logger.debug("Building chat model", extra={"provider": provider.value, "model": model})
        _model_cache[cache_key] = _build(provider, model, settings)

    return _model_cache[cache_key]


def usable_providers(settings: Settings | None = None) -> list[LLMProvider]:
    """Configured providers that actually have the credentials they need."""
    settings = settings or get_settings()
    return [
        provider
        for provider in settings.configured_providers()
        if provider is LLMProvider.OLLAMA or settings.api_key_for(provider)
    ]


def _chain(purpose: Purpose, settings: Settings) -> list[tuple[LLMProvider, BaseChatModel]]:
    """Every usable provider for this purpose, primary first.

    The provider travels alongside its model because the token budget is keyed by
    both, and a fallback draws on a different account's quota entirely.
    """
    providers = usable_providers(settings)
    if not providers:
        configured = ", ".join(p.value for p in settings.configured_providers())
        raise LLMUnavailableError(
            f"No usable LLM provider. Configured: {configured}. "
            "Set the matching API key (for example GOOGLE_API_KEY) in your .env file."
        )

    return [(provider, get_chat_model(purpose, provider, settings)) for provider in providers]


# ── Rate limiting ────────────────────────────────────────────────


def usage_tokens(response: Any) -> int | None:
    """What the provider says the call cost, or ``None`` if it did not say.

    Structured-output runnables hand back a validated schema object rather than a
    message, so the usage is often gone by the time it reaches us. In that case
    the original estimate stands rather than being quietly zeroed.
    """
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
    return None


def _prompt_text(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    parts = [
        flatten_content(getattr(message, "content", message)) for message in as_messages(prompt)
    ]
    return "\n".join(parts)


def _budget_for(provider: LLMProvider, purpose: Purpose, settings: Settings) -> TokenBudget:
    model = model_name_for(provider, purpose, settings)
    return budget_for(provider, model, settings.llm_tokens_per_minute)


def _learn_from(budget: TokenBudget, exc: Exception) -> None:
    """Correct the window against what a rejection says about the real one.

    A rate limit is the only moment the provider states its own figures, and they
    beat ours twice over: the configured ceiling may be wrong for this model, and
    a structured call reconciles against nothing because the usage is stripped off
    with the raw message. Both drifts are fixed here, before the retry waits.
    """
    report = reported_usage(exc)
    if report is None:
        return
    budget.adopt_limit(report.limit)
    budget.resync(report.used)


def _settle(reservation: Reservation, response: Any) -> None:
    actual = usage_tokens(response)
    if actual is not None:
        reservation.settle(actual)


def _metered(
    runnable: Runnable,
    budget: TokenBudget,
    settings: Settings,
    *,
    label: str,
) -> Runnable:
    """Hold a call until the per-minute budget has room for it.

    A failed call keeps its reservation: the provider may well have charged for
    the attempt, and assuming otherwise is how a retry storm starts.
    """
    reserve = max(0, settings.llm_output_reserve)

    def estimate(prompt: Any) -> int:
        return estimate_tokens(_prompt_text(prompt)) + reserve

    def invoke(prompt: Any, config: Any = None) -> Any:
        reservation = budget.reserve_blocking(estimate(prompt))
        try:
            response = runnable.invoke(prompt, config)
        except Exception as exc:
            _learn_from(budget, exc)
            raise
        _settle(reservation, response)
        return response

    async def ainvoke(prompt: Any, config: Any = None) -> Any:
        reservation = await budget.reserve(estimate(prompt))
        try:
            response = await runnable.ainvoke(prompt, config)
        except Exception as exc:
            _learn_from(budget, exc)
            raise
        _settle(reservation, response)
        return response

    return RunnableLambda(invoke, afunc=ainvoke, name=f"budget:{label}")


# ── Retrying and falling through ─────────────────────────────────


def _retried(runnable: Runnable, settings: Settings, *, label: str) -> Runnable:
    """Repeat a call only while repeating it could plausibly help.

    ``with_retry`` selects on exception *type*, which cannot tell a 429 from a
    400 -- both arrive as the same provider error class carrying different text.
    :mod:`llm.errors` reads the status instead, so a deterministic failure costs
    one attempt rather than three, and a rate limit waits the interval the
    provider actually asked for.
    """
    retrier = Retrier(
        settings.llm_max_retries,
        base_delay=settings.llm_retry_backoff_seconds,
        max_delay=settings.llm_retry_max_delay_seconds,
        label=label,
    )

    def invoke(prompt: Any, config: Any = None) -> Any:
        return retrier.run_blocking(lambda: runnable.invoke(prompt, config))

    async def ainvoke(prompt: Any, config: Any = None) -> Any:
        return await retrier.run(lambda: runnable.ainvoke(prompt, config))

    return RunnableLambda(invoke, afunc=ainvoke, name=f"retry:{label}")


def _first_success(
    candidates: Sequence[Runnable], *, label: str, stop_on_abort: bool
) -> Runnable:
    """Try each candidate in order and return the first that answers.

    Two things ``with_fallbacks`` does not do. It re-raises the *first* error,
    which describes the strongest mechanism and so is the one least likely to
    explain why the run ended -- that is how a run whose real problem was a rate
    limit came to be filed under a tool-calling error. And it handles every
    exception alike, so a failure no later candidate could survive still pays for
    all of them.

    ``stop_on_abort`` is true between the strategies for one model, where an
    aborting error rules out every weaker rung as well, and false between
    providers, where the next one has its own key, quota and capabilities.
    """
    if len(candidates) == 1:
        return candidates[0]

    def keep_going(exc: Exception, index: int) -> bool:
        if stop_on_abort and classify(exc) is Disposition.ABORT:
            logger.debug("%s: aborting after candidate %d: %s", label, index, exc)
            return False
        return index < len(candidates) - 1

    def invoke(prompt: Any, config: Any = None) -> Any:
        for index, candidate in enumerate(candidates):
            try:
                return candidate.invoke(prompt, config)
            except Exception as exc:
                if not keep_going(exc, index):
                    raise
        raise AssertionError("unreachable: the last candidate either returns or raises")

    async def ainvoke(prompt: Any, config: Any = None) -> Any:
        for index, candidate in enumerate(candidates):
            try:
                return await candidate.ainvoke(prompt, config)
            except Exception as exc:
                if not keep_going(exc, index):
                    raise
        raise AssertionError("unreachable: the last candidate either returns or raises")

    return RunnableLambda(invoke, afunc=ainvoke, name=f"ladder:{label}")


def get_structured_llm(
    schema: type[BaseModel],
    purpose: Purpose = Purpose.STRUCTURED,
    settings: Settings | None = None,
) -> Runnable:
    """A runnable that returns validated instances of ``schema``.

    Rather than betting on one extraction mechanism, this walks every strategy a
    model supports before moving to the next provider, so swapping the configured
    model never silently costs the ability to produce structured output. See
    :mod:`llm.structured` for the ladder.
    """
    settings = settings or get_settings()

    per_provider: list[Runnable] = []
    for provider, model in _chain(purpose, settings):
        budget = _budget_for(provider, purpose, settings)
        rungs: list[Runnable] = []
        for name, runnable in build_strategies(model, schema):
            logger.debug("Structured strategy available: %s", name)
            label = f"{provider.value}:{name}"
            # Metering sits innermost so every attempt is paced, including retries.
            metered = _metered(runnable, budget, settings, label=label)
            # Every rung is retried, not just the preferred one: a rate limit on
            # the last rung is as transient as one on the first, and giving up
            # there ends the run with no answer at all.
            rungs.append(_retried(metered, settings, label=label))
        per_provider.append(_first_success(rungs, label=provider.value, stop_on_abort=True))

    return _first_success(per_provider, label=purpose.value, stop_on_abort=False)


def get_text_llm(purpose: Purpose = Purpose.TEXT, settings: Settings | None = None) -> Runnable:
    settings = settings or get_settings()
    candidates = [
        _retried(
            _metered(
                model, _budget_for(provider, purpose, settings), settings, label=provider.value
            ),
            settings,
            label=provider.value,
        )
        for provider, model in _chain(purpose, settings)
    ]
    return _first_success(candidates, label=purpose.value, stop_on_abort=False)


def llm_call(prompt: Any, purpose: Purpose = Purpose.TEXT, settings: Settings | None = None) -> str:
    response = get_text_llm(purpose, settings).invoke(prompt)
    return flatten_content(getattr(response, "content", response))


async def allm_call(prompt: Any, purpose: Purpose = Purpose.TEXT, settings: Settings | None = None) -> str:
    response = await get_text_llm(purpose, settings).ainvoke(prompt)
    return flatten_content(getattr(response, "content", response))
