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

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from core.config import LLMProvider, ProviderAccount, Purpose, Settings, get_settings
from core.logging import get_logger
from llm.accounting import Outcome, outcome_for, record_attempt
from llm.budget import Reservation, TokenBudget, budget_for, estimate_tokens
from llm.content import flatten_content
from llm.errors import Disposition, Retrier, classify, reported_usage
from llm.routing import ModelTier, RoutePlan, RouteSignal, plan_for
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


# Which model serves each difficulty tier, when the configuration names none.
#
# Only Groq, because it is the only provider here that publishes two sizes of the
# same family and meters them separately — so routing a small service to the
# smaller model is a real saving against a window it was not going to share
# anyway. MEDIUM and HIGH resolve to the same model deliberately: the account has
# two sizes, not three, and inventing a distinction the provider does not offer
# would make the tier a lie. A provider absent from here is simply not routed.
PROVIDER_TIER_MODELS: dict[LLMProvider, dict[str, str]] = {
    LLMProvider.GROQ: {
        "low": "openai/gpt-oss-20b",
        "medium": "openai/gpt-oss-120b",
        "high": "openai/gpt-oss-120b",
    },
}


class LLMUnavailableError(RuntimeError):
    """No provider is usable, usually a missing API key."""


_model_cache: dict[tuple[LLMProvider, str, float, int], BaseChatModel] = {}


def reset_cache() -> None:
    """Drop cached chat models. Used by tests after changing configuration."""
    _model_cache.clear()


def model_name_for(
    provider: LLMProvider,
    purpose: Purpose,
    settings: Settings | None = None,
    tier: ModelTier | None = None,
) -> str:
    """Which model serves this call.

    Resolution order, and the order matters:

    1. An explicit ``MODEL_HEAVY`` and friends. A named override is a decision
       already taken and outranks any policy.
    2. The tier's model, when routing chose one and a model is configured for it.
    3. The provider's per-purpose default, which is what every call got before
       routing existed and what every unrouted call still gets.
    """
    settings = settings or get_settings()

    override = settings.model_override_for(purpose)
    if override:
        return override

    if tier is not None:
        tiered = tier_model_for(provider, tier, settings)
        if tiered:
            return tiered

    return PROVIDER_DEFAULT_MODELS[provider][purpose]


def tier_model_for(
    provider: LLMProvider, tier: ModelTier, settings: Settings | None = None
) -> str | None:
    """The model configured for one provider and tier, or ``None`` if there is none.

    ``None`` is what makes a tier unavailable on a provider: the router is told
    which tiers exist rather than being asked to assume they all do.
    """
    settings = settings or get_settings()
    return settings.tier_model_override(provider, tier.value) or PROVIDER_TIER_MODELS.get(
        provider, {}
    ).get(tier.value)


def _build(
    provider: LLMProvider,
    model: str,
    settings: Settings,
    ceiling: int | None = None,
    account: ProviderAccount | None = None,
) -> BaseChatModel:
    """Construct a provider client. Imports are local so an uninstalled optional
    provider cannot break importing this module.

    ``ceiling`` is the already-resolved per-purpose output limit. It is passed in
    rather than looked up because the caller has the purpose and the budget needs
    the identical figure; leaving it out falls back to the global ceiling.

    ``account`` supplies the credential. This is the only place in the codebase a
    key is read out of configuration and handed to a provider SDK; everything
    above works with the account's non-secret ``id``.
    """
    key = account.api_key if account is not None else settings.api_key_for(provider)
    temperature = settings.llm_temperature
    timeout = settings.llm_timeout_seconds
    # None hands the decision back to the provider; see Settings for why the
    # default is not the provider's. Each client spells the parameter its own
    # way, and a name a client does not recognise is silently ignored -- which
    # is exactly how a 2048-token cap went unnoticed until a PRD was truncated.
    if ceiling is None:
        ceiling = settings.llm_max_output_tokens
    max_output = ceiling if ceiling > 0 else None

    if provider is LLMProvider.GOOGLE:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=key,
            temperature=temperature,
            timeout=timeout,
            max_output_tokens=max_output,
            max_retries=0,  # retries are handled by the runnable wrapper
        )

    if provider is LLMProvider.GROQ:
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            api_key=key,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_output,
            max_retries=0,
        )

    if provider is LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=key,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_output,
            max_retries=0,
        )

    if provider is LLMProvider.CEREBRAS:
        from langchain_cerebras import ChatCerebras

        return ChatCerebras(
            model=model,
            api_key=key,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_output,
        )

    if provider is LLMProvider.OLLAMA:
        from langchain_ollama import ChatOllama

        # Ollama calls the same idea num_predict.
        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            temperature=temperature,
            num_predict=max_output,
        )

    raise LLMUnavailableError(f"Unsupported provider: {provider}")


def get_chat_model(
    purpose: Purpose,
    provider: LLMProvider,
    settings: Settings | None = None,
    ceiling: int | None = None,
    account: ProviderAccount | None = None,
    tier: ModelTier | None = None,
) -> BaseChatModel:
    """The provider client for this purpose, built once and cached.

    ``ceiling`` is the already-resolved output limit. The registry passes it in
    so `Settings.max_output_for` is consulted exactly once per call path and the
    same number reaches the client and the budget; a caller that omits it gets
    the configured ceiling resolved here, which is what every caller outside the
    registry wants.

    ``account`` selects which of a pooled provider's credentials to build with,
    and defaults to the provider's first — which for an unpooled provider is its
    only one. ``tier`` selects the model, and needs no dimension of its own in the
    cache key because the name it resolves to is already there.
    """
    settings = settings or get_settings()
    model = model_name_for(provider, purpose, settings, tier)
    if ceiling is None:
        ceiling = settings.max_output_for(purpose)
    if account is None:
        account = default_account(provider, settings)

    # Every value that changes the constructed client belongs in the key. The
    # *resolved* ceiling rather than the purpose: two purposes pointed at one
    # model with one ceiling want the same client, and splitting the cache by
    # purpose would build two identical ones. With adaptive ceilings on this is
    # also what stops a small service's client being handed to a large one.
    #
    # The account id for the sharper reason: two Groq accounts run the same model
    # at the same ceiling, so without it the cache would answer a request for
    # account two with a client holding account one's key -- charging the wrong
    # window and, on a revoked key, failing a healthy account. `None` here is the
    # identity of an unpooled provider, so a single-key setup keys exactly as it
    # did before pooling existed.
    cache_key = (provider, account.id, model, settings.llm_temperature, ceiling)

    if cache_key not in _model_cache:
        logger.debug(
            "Building chat model",
            extra={"provider": provider.value, "model": model, "account": str(account)},
        )
        _model_cache[cache_key] = _build(provider, model, settings, ceiling, account)

    return _model_cache[cache_key]


def default_account(provider: LLMProvider, settings: Settings | None = None) -> ProviderAccount:
    """The account to use when a caller does not name one.

    The first configured, which for an unpooled provider is the only one. A
    provider with no credentials at all still yields an account rather than an
    error, so building a client fails where it always did — in the provider SDK,
    saying the key is missing — rather than somewhere new.
    """
    settings = settings or get_settings()
    accounts = settings.accounts_for(provider)
    return accounts[0] if accounts else ProviderAccount(id=None)


def usable_providers(settings: Settings | None = None) -> list[LLMProvider]:
    """Configured providers that actually have the credentials they need."""
    settings = settings or get_settings()
    return [
        provider
        for provider in settings.configured_providers()
        if settings.accounts_for(provider)
    ]


@dataclass(frozen=True)
class Resource:
    """One place a call can actually be sent: a provider, an account, a client.

    The unit the registry reasons about now that a provider can hold several
    independent capacity pools. Its budget is the one shared instance for
    ``(provider, account, model)`` — never a fresh one, and never one per caller.
    """

    provider: LLMProvider
    account: ProviderAccount
    model_name: str
    client: BaseChatModel
    budget: TokenBudget

    @property
    def label(self) -> str:
        """A non-secret name for logs and ladder labels."""
        return self.provider.value if self.account.id is None else self.account.id


def _resources(
    purpose: Purpose,
    settings: Settings,
    ceiling: int | None = None,
    tier: ModelTier | None = None,
) -> list[Resource]:
    """Every place this purpose can be served, primary provider first.

    A provider with numbered keys expands into one resource per account, in
    configured order; a provider with one key is a single resource exactly as it
    was. The provider order is meaningful and preserved — accounts fan out
    *within* a provider, never across one.

    ``ceiling`` is the resolved output limit, passed down rather than looked up
    again so every client is built with the identical figure its budget reserves.
    """
    providers = usable_providers(settings)
    if not providers:
        configured = ", ".join(p.value for p in settings.configured_providers())
        raise LLMUnavailableError(
            f"No usable LLM provider. Configured: {configured}. "
            "Set the matching API key (for example GOOGLE_API_KEY) in your .env file."
        )

    resources: list[Resource] = []
    for provider in providers:
        model_name = model_name_for(provider, purpose, settings, tier)
        for account in settings.accounts_for(provider):
            resources.append(
                Resource(
                    provider=provider,
                    account=account,
                    model_name=model_name,
                    client=get_chat_model(purpose, provider, settings, ceiling, account, tier),
                    budget=budget_for(
                        provider, model_name, settings.llm_tokens_per_minute, account.id
                    ),
                )
            )
    return resources


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


def _budget_for(
    provider: LLMProvider,
    purpose: Purpose,
    settings: Settings,
    account: ProviderAccount | None = None,
) -> TokenBudget:
    """The shared window for this provider, account and model.

    One instance per resource, process-wide — never per agent, per run or per
    stage. Two accounts are two windows because the provider meters them
    separately; two callers on one account are one window for the same reason.
    """
    model = model_name_for(provider, purpose, settings)
    account_id = account.id if account is not None else None
    return budget_for(provider, model, settings.llm_tokens_per_minute, account_id)


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


def _settle(reservation: Reservation, actual: int | None) -> None:
    if actual is not None:
        reservation.settle(actual)


def _metered(
    runnable: Runnable,
    budget: TokenBudget,
    settings: Settings,
    *,
    label: str,
    ceiling: int,
    provider: LLMProvider,
    model_name: str,
    purpose: Purpose,
    account: ProviderAccount | None = None,
    plan: RoutePlan | None = None,
) -> Runnable:
    """Hold a call until the per-minute budget has room for it.

    A failed call keeps its reservation: the provider may well have charged for
    the attempt, and assuming otherwise is how a retry storm starts.

    The reservation covers the output *ceiling*, not just the reserve. Providers
    charge `prompt + max_completion_tokens` against the window, so reserving the
    smaller of the two lets through a request the provider answers with 413 --
    which is what happened at the architecture stage of run 9caeb0c9: the budget
    saw room, Groq saw `Limit 8000, Requested 9200`. A ceiling of 0 means "the
    provider decides", which we cannot count, so the reserve stands alone there.

    ``ceiling`` is the per-purpose figure the client was built with, not the
    global one. Reserving more than the client will ever ask for wastes the
    window; reserving less lets through a request the provider refuses.

    This is also the pipeline's single accounting point (:mod:`llm.accounting`).
    Every attempt passes through here exactly once — including each retry, each
    rung of the structured ladder and each provider fallback — so it is the only
    place a ledger can be both complete and free of double counting. The record
    is written after the attempt has resolved, in both directions, and can never
    affect it: a failure is recorded and then re-raised unchanged.
    """
    reserve = max(0, settings.llm_output_reserve, ceiling)

    def observe(
        prompt_tokens: int, claim: int, started: float, actual: int | None, outcome: Outcome
    ) -> None:
        record_attempt(
            purpose=purpose.value,
            provider=provider.value,
            model=model_name,
            estimated_tokens=prompt_tokens,
            reserved_tokens=claim,
            actual_tokens=actual,
            # The budget wait is pacing, not model time, so the clock starts
            # once the reservation is granted.
            duration_seconds=time.monotonic() - started,
            outcome=outcome,
            # The limit this attempt was actually built with, so a small
            # reservation can be told from a narrowed ceiling.
            ceiling_tokens=ceiling,
            # Which of a pooled provider's accounts paid for it. An identifier,
            # never a credential -- the key does not travel this far.
            account=account.id if account is not None else None,
            # Which tier served it, and which the difficulty score asked for.
            # Both, because a route that stepped down for want of room should not
            # afterwards look like a misjudged score.
            tier=plan.tier.value if plan is not None and plan.tier else None,
            difficulty=(
                plan.difficulty.value if plan is not None and plan.difficulty else None
            ),
            escalation_reason=(
                plan.escalation_reason if plan is not None and plan.escalated else None
            ) or None,
        )

    def invoke(prompt: Any, config: Any = None) -> Any:
        prompt_tokens = estimate_tokens(_prompt_text(prompt))
        claim = prompt_tokens + reserve
        # A call too large for the whole window is refused here, before any
        # attempt is made. Nothing is recorded: no model was asked anything.
        reservation = budget.reserve_blocking(claim)

        started = time.monotonic()
        try:
            response = runnable.invoke(prompt, config)
        except Exception as exc:
            _learn_from(budget, exc)
            observe(prompt_tokens, claim, started, None, outcome_for(exc))
            raise

        actual = usage_tokens(response)
        observe(prompt_tokens, claim, started, actual, Outcome.SUCCESS)
        _settle(reservation, actual)
        return response

    async def ainvoke(prompt: Any, config: Any = None) -> Any:
        prompt_tokens = estimate_tokens(_prompt_text(prompt))
        claim = prompt_tokens + reserve
        reservation = await budget.reserve(claim)

        started = time.monotonic()
        try:
            response = await runnable.ainvoke(prompt, config)
        except Exception as exc:
            _learn_from(budget, exc)
            observe(prompt_tokens, claim, started, None, outcome_for(exc))
            raise

        actual = usage_tokens(response)
        observe(prompt_tokens, claim, started, actual, Outcome.SUCCESS)
        _settle(reservation, actual)
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
    candidates: Sequence[Runnable],
    *,
    label: str,
    stop_on_abort: bool,
    arrange: Callable[[Any], Sequence[Runnable]] | None = None,
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
    providers and between the accounts of one provider — the next has its own
    key, its own quota and its own answer. A revoked credential on one account is
    not evidence about another.

    ``arrange`` reorders the candidates for a given prompt, which is how the
    account pool picks the one with room. It only ever permutes: every candidate
    is still tried, so ordering can improve which attempt succeeds first and can
    never remove a fallback.
    """
    if len(candidates) == 1 and arrange is None:
        return candidates[0]

    def sequence(prompt: Any) -> Sequence[Runnable]:
        return arrange(prompt) if arrange is not None else candidates

    def keep_going(exc: Exception, index: int, total: int) -> bool:
        if stop_on_abort and classify(exc) is Disposition.ABORT:
            logger.debug("%s: aborting after candidate %d: %s", label, index, exc)
            return False
        return index < total - 1

    def invoke(prompt: Any, config: Any = None) -> Any:
        ordered = sequence(prompt)
        for index, candidate in enumerate(ordered):
            try:
                return candidate.invoke(prompt, config)
            except Exception as exc:
                if not keep_going(exc, index, len(ordered)):
                    raise
        raise AssertionError("unreachable: the last candidate either returns or raises")

    async def ainvoke(prompt: Any, config: Any = None) -> Any:
        ordered = sequence(prompt)
        for index, candidate in enumerate(ordered):
            try:
                return await candidate.ainvoke(prompt, config)
            except Exception as exc:
                if not keep_going(exc, index, len(ordered)):
                    raise
        raise AssertionError("unreachable: the last candidate either returns or raises")

    return RunnableLambda(invoke, afunc=ainvoke, name=f"ladder:{label}")


def _reserve_for(settings: Settings, ceiling: int) -> int:
    """What one call holds against a window: the output allowance it may use.

    Shared by `_metered`, which reserves it, and by the pool, which asks whether
    an account has room for it. The two must agree or the pool would pick an
    account on a figure the meter then exceeds.
    """
    return max(0, settings.llm_output_reserve, ceiling)


def _eligible_tiers(
    purpose: Purpose, settings: Settings, reserve: int
) -> frozenset[ModelTier]:
    """Which tiers could actually serve this call right now.

    A tier qualifies when some usable provider names a model for it and some
    account of that provider could take the reservation: its window is large
    enough to hold the call at all, and has room for it at the moment.

    Read here rather than in the policy because reading a budget is the
    registry's business — `llm.routing` is handed the answer and never goes
    looking. The reading is a snapshot: an account can fill between here and the
    call, which costs a wait exactly as it always has. The meter, not this, is
    what admits a call.
    """
    eligible: set[ModelTier] = set()

    for provider in usable_providers(settings):
        for tier in ModelTier:
            model = tier_model_for(provider, tier, settings)
            if not model:
                continue
            for account in settings.accounts_for(provider):
                budget = budget_for(
                    provider, model, settings.llm_tokens_per_minute, account.id
                )
                if budget.limit >= reserve and budget.remaining() >= reserve:
                    eligible.add(tier)
                    break

    return frozenset(eligible)


def _route(
    purpose: Purpose,
    signal: RouteSignal | None,
    settings: Settings,
    reserve: int,
) -> RoutePlan:
    """Decide the tier for this call, and say so once in the run log.

    The eligibility set is computed only when routing could actually apply, so a
    disabled flag or an unrouted purpose costs nothing at all — not even a budget
    lookup.
    """
    if not settings.routing_enabled or signal is None:
        return plan_for(purpose, signal, settings)

    plan = plan_for(purpose, signal, settings, _eligible_tiers(purpose, settings, reserve))
    if plan.routed:
        logger.info("Routing %s", plan.describe())
    return plan


def _pooled(
    candidates: list[tuple[Runnable, TokenBudget]],
    settings: Settings,
    *,
    label: str,
    reserve: int,
) -> Runnable:
    """Spread a provider's calls across its accounts by who has room.

    A provider meters per account, so several keys are several independent
    windows. The policy is deliberately the simplest thing that uses them
    honestly, and no more: an account that can take the call now is preferred
    over one that cannot, the roomiest of those goes first, and ties keep the
    configured order because ``list.sort`` is stable. Nothing here looks at the
    task, the model or any history — those are later phases.

    Accounts without room are appended rather than dropped. If none has room the
    call still goes to the roomiest and waits on its budget, which is what a
    single account has always done; refusing instead would turn a pause into a
    failure.

    Reordering is advisory. Every account is still attempted in turn by
    `_first_success`, so a wrong guess costs position rather than a fallback.
    """
    if len(candidates) == 1:
        return candidates[0][0]

    def arrange(prompt: Any) -> Sequence[Runnable]:
        estimate = estimate_tokens(_prompt_text(prompt)) + reserve

        # Each window is read once, so the ordering and the eligibility test are
        # decided on the same figures. Read outside any lock: a budget can move
        # between here and the reservation, which costs a queue position and
        # never correctness -- the meter is still what admits the call.
        headroom = [(runnable, budget.remaining()) for runnable, budget in candidates]
        # sorted() is stable, so accounts with equal room keep configured order.
        ranked = sorted(headroom, key=lambda pair: -pair[1])

        ready = [runnable for runnable, room in ranked if room >= estimate]
        waiting = [runnable for runnable, room in ranked if room < estimate]
        return ready + waiting

    return _first_success(
        [runnable for runnable, _ in candidates],
        label=label,
        stop_on_abort=False,
        arrange=arrange,
    )


def get_structured_llm(
    schema: type[BaseModel],
    purpose: Purpose = Purpose.STRUCTURED,
    settings: Settings | None = None,
    demand: int | None = None,
    signal: RouteSignal | None = None,
) -> Runnable:
    """A runnable that returns validated instances of ``schema``.

    Rather than betting on one extraction mechanism, this walks every strategy a
    model supports before moving to the next provider, so swapping the configured
    model never silently costs the ability to produce structured output. See
    :mod:`llm.structured` for the ladder.

    ``demand`` is what the caller knows about the size of the answer it wants.
    The ceiling is resolved from it once, here, and the same figure is then given
    to every client in the chain and to every meter around them — which is the
    invariant that keeps the provider's limit and the budget's reservation
    describing the same request.

    ``signal`` is what the caller knows about the *difficulty* of the task, and
    picks the model tier. The two are independent and resolved in that order: the
    ceiling first, because how large the answer may be does not depend on which
    model writes it, and the tier second against a reservation already known. A
    route can therefore never assume one ceiling while the meter reserves another.
    """
    settings = settings or get_settings()

    # Resolved once. Everything below is handed this number rather than asking
    # for its own.
    ceiling = settings.max_output_for(purpose, demand)
    reserve = _reserve_for(settings, ceiling)
    plan = _route(purpose, signal, settings, reserve)

    # Three nested ladders, each answering a different question. Innermost: which
    # extraction mechanism does this model support. Then: which of a provider's
    # accounts has room. Outermost: which provider. A provider with one account
    # collapses to exactly the two-level ladder it has always been.
    per_provider: list[Runnable] = []
    for provider, group in _grouped_by_provider(
        _resources(purpose, settings, ceiling, plan.tier)
    ):
        per_account: list[tuple[Runnable, TokenBudget]] = []

        for resource in group:
            rungs: list[Runnable] = []
            for name, runnable in build_strategies(resource.client, schema):
                logger.debug("Structured strategy available: %s", name)
                label = f"{resource.label}:{name}"
                # Metering sits innermost so every attempt is paced, including retries.
                metered = _metered(
                    runnable,
                    resource.budget,
                    settings,
                    label=label,
                    ceiling=ceiling,
                    provider=provider,
                    model_name=resource.model_name,
                    purpose=purpose,
                    account=resource.account,
                    plan=plan,
                )
                # Every rung is retried, not just the preferred one: a rate limit
                # on the last rung is as transient as one on the first, and giving
                # up there ends the run with no answer at all.
                rungs.append(_retried(metered, settings, label=label))

            ladder = _first_success(rungs, label=resource.label, stop_on_abort=True)
            per_account.append((ladder, resource.budget))

        per_provider.append(
            _pooled(per_account, settings, label=provider.value, reserve=reserve)
        )

    return _first_success(per_provider, label=purpose.value, stop_on_abort=False)


def get_text_llm(
    purpose: Purpose = Purpose.TEXT,
    settings: Settings | None = None,
    demand: int | None = None,
) -> Runnable:
    settings = settings or get_settings()
    ceiling = settings.max_output_for(purpose, demand)
    reserve = _reserve_for(settings, ceiling)

    per_provider: list[Runnable] = []
    for provider, group in _grouped_by_provider(_resources(purpose, settings, ceiling)):
        per_account = [
            (
                _retried(
                    _metered(
                        resource.client,
                        resource.budget,
                        settings,
                        label=resource.label,
                        ceiling=ceiling,
                        provider=provider,
                        model_name=resource.model_name,
                        purpose=purpose,
                        account=resource.account,
                    ),
                    settings,
                    label=resource.label,
                ),
                resource.budget,
            )
            for resource in group
        ]
        per_provider.append(
            _pooled(per_account, settings, label=provider.value, reserve=reserve)
        )

    return _first_success(per_provider, label=purpose.value, stop_on_abort=False)


def _grouped_by_provider(
    resources: Sequence[Resource],
) -> list[tuple[LLMProvider, list[Resource]]]:
    """Resources gathered by provider, keeping the configured provider order.

    Accounts fan out inside a provider and never across one: falling back to a
    different provider is a different decision from spreading load over one
    provider's keys, and the ladder keeps them apart.
    """
    grouped: dict[LLMProvider, list[Resource]] = {}
    for resource in resources:
        grouped.setdefault(resource.provider, []).append(resource)
    return list(grouped.items())


def llm_call(prompt: Any, purpose: Purpose = Purpose.TEXT, settings: Settings | None = None) -> str:
    response = get_text_llm(purpose, settings).invoke(prompt)
    return flatten_content(getattr(response, "content", response))


async def allm_call(prompt: Any, purpose: Purpose = Purpose.TEXT, settings: Settings | None = None) -> str:
    response = await get_text_llm(purpose, settings).ainvoke(prompt)
    return flatten_content(getattr(response, "content", response))
