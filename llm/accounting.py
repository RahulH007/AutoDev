"""What every model attempt cost, recorded as it happens.

This is an accountant, not a controller. :mod:`llm.budget` decides whether a call
may proceed and holds it back when the window is full; nothing here ever gates,
delays or refuses anything. It watches the same moment from the side and writes
down what happened, so a later change to the pipeline can be judged against a
figure rather than an impression.

There is exactly one observation point, :func:`llm.registry._metered`, because
that is already the one place every model call passes through — including every
retry, every rung of the structured-output ladder, and every provider fallback.
Instrumenting anywhere else would either miss calls or count them twice.

Three things are deliberately *not* done here.

**No second counter.** The reservation the budget computes is the reservation
recorded; this module never re-estimates anything.

**No pretending.** A structured call hands back a validated schema object with the
provider's usage stripped off, so ``actual_tokens`` is genuinely unknown for most
of the pipeline. That is recorded as ``None`` and aggregated as "unavailable",
never quietly replaced with the estimate — the whole point of measuring is to
know which figures are real.

**No invented pricing.** This repository has no pricing table, and a made-up
per-token rate would produce a number that looks authoritative and is not. Cost
is reported as ``None`` until a real pricing source exists.

Scope is per stage. :func:`recording` binds a fresh ledger to a
:class:`~contextvars.ContextVar`, so two runs executing concurrently in the
server cannot see each other's ledger: each run is driven by its own asyncio
task, and a task gets its own copy of the context. Nothing is process-global.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from llm.errors import classify


class Outcome(StrEnum):
    """How one attempt ended.

    The three failure values are the dispositions :func:`llm.errors.classify`
    already assigns, reused rather than reinvented so the ledger explains a
    failure in the same vocabulary the retry logic acted on. ``FAILED`` is the
    fallback for a disposition this enum does not know, so a new one can never
    raise inside an exception handler and mask the original error.
    """

    SUCCESS = "success"
    RETRY = "retry"
    DEGRADE = "degrade"
    ABORT = "abort"
    FAILED = "failed"


def outcome_for(exc: BaseException) -> Outcome:
    """The outcome to record for a failed attempt."""
    try:
        return Outcome(classify(exc).value)
    except ValueError:  # pragma: no cover - a disposition this enum lacks
        return Outcome.FAILED


@dataclass(frozen=True)
class CallRecord:
    """One attempt at one model.

    ``estimated_tokens`` is the prompt as the budget sized it. ``reserved_tokens``
    is what was actually claimed against the per-minute window: the prompt plus
    the output allowance, which is the figure the provider charges and therefore
    the one that decides whether a run fits its rate limit.

    ``actual_tokens`` is what the provider said it billed, and is ``None``
    whenever it did not say.

    ``duration_seconds`` times the model call alone. Waiting for room in the
    budget is pacing, not model time, so the clock starts once the reservation is
    granted — otherwise a slow window would read as a slow model.

    ``ceiling_tokens`` is the output limit this attempt was actually built with,
    which is the configured figure unless adaptive ceilings narrowed it. Recorded
    because ``reserved_tokens`` alone cannot say whether a small reservation came
    from a short prompt or from a narrowed ceiling.

    ``account`` names which set of a provider's credentials handled the call —
    ``"groq-2"``, say — because a pooled provider meters each one separately and
    "which account paid for this" is otherwise unanswerable. It is an identifier
    and never a credential; the key itself never reaches this module. ``None``
    for a provider configured with a single unnumbered key, which is every
    provider until someone asks for a pool.
    """

    run_id: str
    stage: str
    purpose: str
    provider: str
    model: str
    estimated_tokens: int
    reserved_tokens: int
    actual_tokens: int | None
    duration_seconds: float
    outcome: str
    ceiling_tokens: int = 0
    account: str | None = None
    # Which model tier served the call, and which the difficulty score asked for.
    # Both ``None`` unless difficulty routing chose the model, which is what keeps
    # a report from a run that did not route looking exactly as it always did.
    # They differ only when a tier was stepped down for want of a configured model
    # or of room, and the pair is what makes that visible afterwards.
    tier: str | None = None
    difficulty: str | None = None
    # Why this call was given a stronger model than its difficulty asked for.
    # Set only on an escalated call, so ``difficulty`` and ``tier`` together with
    # this answer "what did it start on, what did it end on, and why".
    escalation_reason: str | None = None


def empty_totals() -> dict[str, Any]:
    """A well-formed report for a scope in which nothing has been recorded."""
    return {
        "calls": 0,
        "estimated_tokens": 0,
        "reserved_tokens": 0,
        "actual_tokens": None,
        "calls_with_usage": 0,
        "seconds": 0.0,
        "estimated_cost": None,
        "outcomes": {},
    }


def _totals_of(records: Iterable[CallRecord]) -> dict[str, Any]:
    totals = empty_totals()
    actual = 0

    for record in records:
        totals["calls"] += 1
        totals["estimated_tokens"] += record.estimated_tokens
        totals["reserved_tokens"] += record.reserved_tokens
        totals["seconds"] += record.duration_seconds
        totals["outcomes"][record.outcome] = totals["outcomes"].get(record.outcome, 0) + 1
        if record.actual_tokens is not None:
            totals["calls_with_usage"] += 1
            actual += record.actual_tokens

    # None rather than 0: no provider reported usage, which is not the same as a
    # call that genuinely cost nothing.
    if totals["calls_with_usage"]:
        totals["actual_tokens"] = actual

    totals["seconds"] = round(totals["seconds"], 3)
    return totals


def _group(records: Iterable[CallRecord], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[CallRecord]] = {}
    for record in records:
        buckets.setdefault(getattr(record, key), []).append(record)
    return {name: _totals_of(bucket) for name, bucket in buckets.items()}


class Ledger:
    """Every attempt made inside one scope, and the totals over them.

    Appending is the only mutation, and CPython makes ``list.append`` atomic, so
    the sequential awaits of a single stage need no lock. Two stages never share
    a ledger — see :func:`recording`.
    """

    def __init__(self, run_id: str = "", stage: str = "") -> None:
        self.run_id = run_id
        self.stage = stage
        self._records: list[CallRecord] = []

    @property
    def records(self) -> list[CallRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def record(
        self,
        *,
        purpose: str,
        provider: str,
        model: str,
        estimated_tokens: int,
        reserved_tokens: int,
        actual_tokens: int | None,
        duration_seconds: float,
        outcome: Outcome | str,
        ceiling_tokens: int = 0,
        account: str | None = None,
        tier: str | None = None,
        difficulty: str | None = None,
        escalation_reason: str | None = None,
        run_id: str | None = None,
        stage: str | None = None,
    ) -> CallRecord:
        """Write down one attempt. ``run_id`` and ``stage`` default to the scope's."""
        entry = CallRecord(
            run_id=self.run_id if run_id is None else run_id,
            stage=self.stage if stage is None else stage,
            purpose=purpose,
            provider=provider,
            model=model,
            estimated_tokens=int(estimated_tokens),
            reserved_tokens=int(reserved_tokens),
            actual_tokens=None if actual_tokens is None else int(actual_tokens),
            duration_seconds=max(0.0, float(duration_seconds)),
            outcome=str(getattr(outcome, "value", outcome)),
            ceiling_tokens=max(0, int(ceiling_tokens)),
            account=account,
            tier=tier,
            difficulty=difficulty,
            escalation_reason=escalation_reason,
        )
        self._records.append(entry)
        return entry

    # ── Aggregates ───────────────────────────────────────────────

    def totals(self) -> dict[str, Any]:
        return _totals_of(self._records)

    def by_run(self) -> dict[str, dict[str, Any]]:
        return _group(self._records, "run_id")

    def by_stage(self) -> dict[str, dict[str, Any]]:
        return _group(self._records, "stage")

    def by_purpose(self) -> dict[str, dict[str, Any]]:
        return _group(self._records, "purpose")

    def by_model(self) -> dict[str, dict[str, Any]]:
        """Totals per provider and model, keyed ``provider:model``.

        Keyed by both because the same model name on two providers draws on two
        different accounts, exactly as the token budget is keyed.
        """
        buckets: dict[str, list[CallRecord]] = {}
        for record in self._records:
            buckets.setdefault(f"{record.provider}:{record.model}", []).append(record)
        return {name: _totals_of(bucket) for name, bucket in buckets.items()}

    def by_account(self) -> dict[str, dict[str, Any]]:
        """Totals per provider account, keyed ``provider:account``.

        Only present for a pooled provider. A provider with a single unnumbered
        key has no account identity to report, and inventing one would put a
        section in every report that has never meant anything.
        """
        buckets: dict[str, list[CallRecord]] = {}
        for record in self._records:
            if record.account:
                buckets.setdefault(f"{record.provider}:{record.account}", []).append(record)
        return {name: _totals_of(bucket) for name, bucket in buckets.items()}

    def by_tier(self) -> dict[str, dict[str, Any]]:
        """Totals per model tier. Only present when difficulty routing chose one."""
        buckets: dict[str, list[CallRecord]] = {}
        for record in self._records:
            if record.tier:
                buckets.setdefault(record.tier, []).append(record)
        return {name: _totals_of(bucket) for name, bucket in buckets.items()}

    def report(self) -> dict[str, Any]:
        """The serialisable shape carried in graph state as ``cost_report``.

        ``by_model`` keeps its ``provider:model`` keys rather than gaining an
        account or tier segment, because a report written before either existed
        has to stay readable. Both get a section of their own instead, and each is
        absent entirely when nothing used it.
        """
        report = {
            **self.totals(),
            "by_stage": self.by_stage(),
            "by_model": self.by_model(),
        }
        for name, section in (("by_account", self.by_account()), ("by_tier", self.by_tier())):
            if section:
                report[name] = section
        return report


def merge_reports(base: dict[str, Any] | None, addition: dict[str, Any] | None) -> dict[str, Any]:
    """Fold one stage's report into the run's running total.

    The pipeline accumulates across stages, retries and review pauses, and a
    resumed run starts a fresh ledger, so the durable total is the merged one in
    graph state rather than any single ledger.

    ``actual_tokens`` stays ``None`` unless at least one side actually had a
    figure; adding an unavailable value as zero would understate the real usage.
    """
    merged = empty_totals()
    merged["by_stage"] = {}
    merged["by_model"] = {}
    optional: dict[str, dict[str, Any]] = {"by_account": {}, "by_tier": {}}

    for report in (base or {}, addition or {}):
        if not report:
            continue
        merged["calls"] += int(report.get("calls") or 0)
        merged["estimated_tokens"] += int(report.get("estimated_tokens") or 0)
        merged["reserved_tokens"] += int(report.get("reserved_tokens") or 0)
        merged["calls_with_usage"] += int(report.get("calls_with_usage") or 0)
        merged["seconds"] += float(report.get("seconds") or 0.0)

        actual = report.get("actual_tokens")
        if actual is not None:
            merged["actual_tokens"] = int(actual) + int(merged["actual_tokens"] or 0)

        for outcome, count in (report.get("outcomes") or {}).items():
            merged["outcomes"][outcome] = merged["outcomes"].get(outcome, 0) + int(count)

        for field in ("by_stage", "by_model"):
            for name, totals in (report.get(field) or {}).items():
                merged[field][name] = _merge_totals(merged[field].get(name), totals)

        # Kept apart from the loop above so each stays absent when neither side
        # had one -- a report that never pooled or routed keeps its old shape.
        for field, collected in optional.items():
            for name, totals in (report.get(field) or {}).items():
                collected[name] = _merge_totals(collected.get(name), totals)

    merged["seconds"] = round(merged["seconds"], 3)
    for field, collected in optional.items():
        if collected:
            merged[field] = collected
    return merged


def _merge_totals(base: dict[str, Any] | None, addition: dict[str, Any]) -> dict[str, Any]:
    """Merge two totals dicts, without the nested breakdowns."""
    merged = merge_reports(base, addition)
    for nested in ("by_stage", "by_model", "by_account", "by_tier"):
        merged.pop(nested, None)
    return merged


# ── Scope ────────────────────────────────────────────────────────

# Never read or written except through the helpers below. A ContextVar rather
# than a module-level ledger because the server drives each run in its own
# asyncio task: a task inherits a copy of the context at creation, so run A's
# ledger is invisible to run B no matter how the two interleave.
_ledger: ContextVar[Ledger | None] = ContextVar("llm_ledger", default=None)


def current_ledger() -> Ledger | None:
    """The ledger for the stage currently executing, if one is bound."""
    return _ledger.get()


@contextmanager
def recording(run_id: str = "", stage: str = "") -> Iterator[Ledger]:
    """Collect every model attempt made inside this block into a fresh ledger."""
    ledger = Ledger(run_id=run_id, stage=stage)
    token = _ledger.set(ledger)
    try:
        yield ledger
    finally:
        _ledger.reset(token)


def record_attempt(**fields: Any) -> CallRecord | None:
    """Record an attempt against the bound ledger, or do nothing if there is none.

    A model call made outside a pipeline stage — a script, a test, a direct
    registry call — is simply not accounted for. Measurement must never be the
    reason a call fails, so this returns quietly rather than raising.
    """
    ledger = _ledger.get()
    if ledger is None:
        return None
    return ledger.record(**fields)
