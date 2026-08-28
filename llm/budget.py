"""A client-side ceiling on tokens per minute.

Providers meter tokens over a rolling window and reject anything that crosses the
line, so a pipeline either paces itself or discovers the limit as a failed run.
This is the pacing: reserve before the call, wait if there is no room, and
reconcile against real usage afterwards.

The ceiling belongs to a provider account, not to an agent. Groq meters per
organisation, so every agent on one model draws from the same instance --
four agents holding four private budgets would commit four times the real quota.

The window rolls rather than resetting. A fixed window that clears on the minute
lets a burst either side of the boundary put twice the limit into the provider's
own rolling window, which is the failure this exists to prevent.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.config import LLMProvider
from core.logging import get_logger

logger = get_logger(__name__)

WINDOW_SECONDS = 60.0

# Roughly four characters to a token across the models used here. Deliberately a
# little pessimistic: over-reserving costs a wait, under-reserving costs the run.
CHARS_PER_TOKEN = 4
MIN_ESTIMATE = 16


class BudgetExceededError(RuntimeError):
    """A single call is larger than the entire per-minute budget.

    Waiting cannot help -- an empty window still would not fit it. The work has to
    be split into smaller calls, or the budget raised.
    """


def estimate_tokens(text: str) -> int:
    """Approximate the tokens in a prompt, before any provider has seen it."""
    return max(MIN_ESTIMATE, len(text) // CHARS_PER_TOKEN)


@dataclass
class _Charge:
    at: float
    tokens: int


class Reservation:
    """A claim on the window, replaceable with the true cost once it is known."""

    def __init__(self, charge: _Charge) -> None:
        self._charge = charge

    def settle(self, actual_tokens: int) -> None:
        """Swap the estimate for what the provider actually billed."""
        self._charge.tokens = max(0, int(actual_tokens))


class TokenBudget:
    """A rolling token-per-minute window shared by one provider and model.

    ``clock`` and ``sleep`` are injectable so tests can drive the window without
    waiting in real time.
    """

    def __init__(
        self,
        tokens_per_minute: int,
        *,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        blocking_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._limit = int(tokens_per_minute)
        self._window = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._blocking_sleep = blocking_sleep
        self._charges: list[_Charge] = []
        # The pipeline is async; the blocking path exists only for synchronous
        # callers, and the two are never in flight at once, so a lock each is
        # enough without coordinating between them.
        self._lock = asyncio.Lock()
        self._blocking_lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def used(self) -> int:
        """Tokens charged inside the current window."""
        self._prune()
        return sum(charge.tokens for charge in self._charges)

    def remaining(self) -> int:
        """Headroom left in the current window."""
        return max(0, self._limit - self.used())

    def adopt_limit(self, limit: int) -> None:
        """Take the provider's word for its own ceiling.

        A configured limit is a guess, and a wrong one cannot be counted around:
        the pipeline paced against 12,000 while Groq enforced 8,000 and was
        rejected with its own window still showing room to spare. The rejection
        says which number is real, so believe it -- in both directions, since the
        provider is equally authoritative when the true ceiling is higher.
        """
        limit = int(limit)
        if limit <= 0 or limit == self._limit:
            return
        logger.info(
            "Adopting the provider's stated token ceiling: %d per minute (was %d)",
            limit,
            self._limit,
        )
        self._limit = limit

    def resync(self, used_tokens: int) -> None:
        """Replace our estimate of the window with what the provider reports.

        Estimates drift -- structured calls hand back a schema object with the
        usage already stripped off, so there is nothing to reconcile against and
        the guess stands for the whole minute. A rejection states the real
        figure, which is worth more than any amount of careful local counting.

        The whole reported total is charged as of now. Its real age is unknowable
        and dating it to the present holds it for a full window, which errs
        towards waiting slightly too long rather than being rejected again.
        """
        self._charges = []
        if used_tokens > 0:
            self.record_now(used_tokens)

    def record_now(self, tokens: int) -> Reservation:
        """Charge tokens immediately, without waiting for room.

        For usage that has already happened and cannot be un-spent.
        """
        charge = _Charge(at=self._clock(), tokens=max(0, int(tokens)))
        self._charges.append(charge)
        return Reservation(charge)

    async def reserve(self, estimated: int) -> Reservation:
        """Wait until ``estimated`` tokens fit, then claim them."""
        estimated = self._admit(estimated)

        # The wait happens under the lock on purpose: releasing it would let every
        # queued caller wake against the same headroom and collectively overshoot.
        async with self._lock:
            wait = self._wait_for(estimated)
            if wait > 0:
                self._announce(wait)
                await self._sleep(wait)
            return self._claim(estimated)

    def reserve_blocking(self, estimated: int) -> Reservation:
        """The synchronous twin of :meth:`reserve`, for callers outside the loop."""
        estimated = self._admit(estimated)

        with self._blocking_lock:
            wait = self._wait_for(estimated)
            if wait > 0:
                self._announce(wait)
                self._blocking_sleep(wait)
            return self._claim(estimated)

    # ── Internals ────────────────────────────────────────────────

    def _admit(self, estimated: int) -> int:
        """Normalise the request and reject one that could never fit."""
        estimated = max(0, int(estimated))
        if estimated > self._limit:
            raise BudgetExceededError(
                f"A single call needs {estimated} tokens but the per-minute budget is "
                f"{self._limit}. Split the work into smaller calls or raise "
                "LLM_TOKENS_PER_MINUTE."
            )
        return estimated

    def _claim(self, estimated: int) -> Reservation:
        charge = _Charge(at=self._clock(), tokens=estimated)
        self._charges.append(charge)
        return Reservation(charge)

    def _announce(self, wait: float) -> None:
        """Say why the run has gone quiet; this reaches the console's live log."""
        logger.info(
            "Token budget reached; waiting %.1fs for room (%d of %d in the last minute)",
            wait,
            self.used(),
            self._limit,
        )

    def _prune(self) -> None:
        cutoff = self._clock() - self._window
        self._charges = [charge for charge in self._charges if charge.at > cutoff]

    def _wait_for(self, estimated: int) -> float:
        """Seconds until ``estimated`` tokens fit, expiring oldest charges first."""
        self._prune()
        total = sum(charge.tokens for charge in self._charges)
        if total + estimated <= self._limit:
            return 0.0

        now = self._clock()
        for charge in sorted(self._charges, key=lambda c: c.at):
            total -= charge.tokens
            if total + estimated <= self._limit:
                return max(0.0, charge.at + self._window - now)

        return 0.0  # Unreachable: a call larger than the limit is refused above.


# ── Shared registry ──────────────────────────────────────────────

_budgets: dict[tuple[LLMProvider, str], TokenBudget] = {}


def budget_for(provider: LLMProvider, model: str, tokens_per_minute: int) -> TokenBudget:
    """The one budget for this provider and model.

    Keyed the same way as the model cache, and shared for the same reason: the
    quota is a property of the account, not of the caller.
    """
    key = (provider, model)
    if key not in _budgets:
        _budgets[key] = TokenBudget(tokens_per_minute)
    return _budgets[key]


def reset_budgets() -> None:
    """Drop every budget. Used by tests between cases."""
    _budgets.clear()
