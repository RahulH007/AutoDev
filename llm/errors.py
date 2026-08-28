"""Deciding what a failed model call means, and what to do about it.

Not every failure is worth trying again. A 429 is: the same call in ten seconds
may well succeed. A 400 saying the model would not call a tool is not — the
prompt, the model and the schema are all unchanged, so the second attempt fails
exactly like the first, and the third like the second. Retrying it costs nothing
but the rate-limit budget the next agent needs.

That is not hypothetical. In the live run this module exists to fix, nine model
calls were made and four of them were identical repeats of a deterministic 400.
Those four filled the token window, which forced three waits totalling over two
minutes, which left nothing for the one strategy that could still have worked.

So each error gets one of three dispositions:

``RETRY``    -- transient. The same call may succeed shortly.
``DEGRADE``  -- this model cannot do what was asked of it in this way. Fall
                through to the next structured-output strategy or provider now,
                without repeating a call whose answer is already known.
``ABORT``    -- nothing further along helps either: a rejected key, a model that
                no longer exists, a call too large for the whole budget.

Unknown errors degrade rather than retry. Falling through costs one attempt on
the next rung; retrying an unknown deterministic failure costs the budget.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from core.logging import get_logger
from llm.budget import BudgetExceededError

logger = get_logger(__name__)

T = TypeVar("T")

BASE_DELAY_SECONDS = 2.0
# A provider that asks for a longer pause than this is telling us to come back
# much later than the run can usefully wait. The token budget handles sustained
# pacing; this only smooths over a brief spike.
MAX_DELAY_SECONDS = 60.0

# Providers state the status in the message when the SDK exception is re-raised
# by LangChain as a plain one: "Error code: 429 - {...}". Anchoring on the label
# stops a token count in the body ("Limit 12000") being read as a status.
_STATUS_IN_MESSAGE = re.compile(r"(?:error|status)[ _]?code\D{0,3}(\d{3})\b", re.IGNORECASE)

# "Please try again in 10.33s." / "...in 1m30s."
_TRY_AGAIN = re.compile(r"try\s+again\s+in\s+([0-9][0-9a-z.\s]*)", re.IGNORECASE)
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)(?![a-z])", re.IGNORECASE)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})
_FATAL_STATUS = frozenset({401, 402, 403, 404})

# Provider SDKs and httpx do not subclass the builtin TimeoutError or
# ConnectionError, so the class name is the only portable signal.
_TRANSIENT_NAME = re.compile(r"timeout|timedout|connection|unavailable|overload", re.IGNORECASE)
_TRANSIENT_MESSAGE = re.compile(
    r"timed out|timeout|connection (?:reset|aborted|refused|error)|"
    r"rate.?limit|too many requests|overloaded|temporarily unavailable",
    re.IGNORECASE,
)


# A rate-limited provider states the window it is actually enforcing:
# "on tokens per minute (TPM): Limit 8000, Used 5276, Requested 5857". Anchoring
# on "tokens per minute" matters -- the same sentence shape carries the requests
# per minute limit, and adopting *its* "Limit 30" as a token ceiling would stall
# every call the pipeline makes.
_TPM_REPORT = re.compile(
    r"tokens?\s+per\s+minute[^:]*:\s*Limit\s+(\d+),\s*Used\s+(\d+),\s*Requested\s+(\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UsageReport:
    """What the provider says its own rolling window currently holds."""

    limit: int
    used: int
    requested: int


def reported_usage(exc: BaseException) -> UsageReport | None:
    """The token window the provider says it is enforcing, if it said.

    This is ground truth, and worth more than any local estimate: a configured
    ceiling can simply be wrong for the model in use, and no amount of careful
    counting against the wrong number avoids a 429.
    """
    match = _TPM_REPORT.search(str(exc))
    if not match:
        return None
    limit, used, requested = (int(group) for group in match.groups())
    return UsageReport(limit=limit, used=used, requested=requested)


class Disposition(StrEnum):
    """What to do with a failed call."""

    RETRY = "retry"  # transient; the same call may succeed shortly
    DEGRADE = "degrade"  # this rung cannot do it; the next one might
    ABORT = "abort"  # nothing in the ladder will help


def status_code(exc: BaseException) -> int | None:
    """The HTTP status behind an exception, however it was carried."""
    for attribute in ("status_code", "http_status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value

    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value <= 599:
        return value

    match = _STATUS_IN_MESSAGE.search(str(exc))
    return int(match.group(1)) if match else None


def _header(exc: BaseException, name: str) -> str | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        items = headers.items()
    except AttributeError:  # pragma: no cover - not a mapping
        return None
    wanted = name.lower()
    for key, value in items:
        if str(key).lower() == wanted:
            return str(value)
    return None


def _parse_duration(text: str) -> float | None:
    parts = _DURATION_PART.findall(text)
    if parts:
        return sum(float(value) * _UNIT_SECONDS[unit.lower()] for value, unit in parts)
    return None


def retry_after(exc: BaseException) -> float | None:
    """How long the provider asked us to wait, in seconds, if it said.

    The ``Retry-After`` header comes first; Groq puts the same figure in the
    message body, which is all that survives once LangChain re-raises the error.
    An HTTP-date header is ignored rather than guessed at -- a wrong wait is
    worse than falling back to plain backoff.
    """
    header = _header(exc, "retry-after")
    if header is not None:
        try:
            return max(0.0, float(header.strip()))
        except ValueError:
            pass  # An HTTP-date, or something else we should not guess at.

    match = _TRY_AGAIN.search(str(exc))
    if match:
        return _parse_duration(match.group(1))
    return None


def classify(exc: BaseException) -> Disposition:
    """Decide what a failure means. See the module docstring for the three cases."""
    if isinstance(exc, BudgetExceededError):
        # Waiting cannot make an oversized call fit, and every rung below this
        # one sends a *longer* prompt, so degrading is no better than retrying.
        return Disposition.ABORT

    status = status_code(exc)
    if status is not None:
        if status in _FATAL_STATUS:
            return Disposition.ABORT
        if status in _RETRYABLE_STATUS or status >= 500:
            return Disposition.RETRY
        return Disposition.DEGRADE  # any other 4xx: the request itself is wrong

    if isinstance(exc, TimeoutError | ConnectionError):
        return Disposition.RETRY
    if _TRANSIENT_NAME.search(type(exc).__name__):
        return Disposition.RETRY
    if _TRANSIENT_MESSAGE.search(str(exc)):
        return Disposition.RETRY

    return Disposition.DEGRADE


class Retrier:
    """Repeats a call only while repeating it could plausibly help.

    ``sleep`` and ``blocking_sleep`` are injectable so tests assert the waits
    this decides on without spending them.
    """

    def __init__(
        self,
        attempts: int,
        *,
        base_delay: float = BASE_DELAY_SECONDS,
        max_delay: float = MAX_DELAY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        blocking_sleep: Callable[[float], None] = time.sleep,
        label: str = "llm",
    ) -> None:
        self._attempts = max(1, int(attempts))
        self._base = base_delay
        self._max = max_delay
        self._sleep = sleep
        self._blocking_sleep = blocking_sleep
        self._label = label

    def delay_for(self, exc: BaseException, attempt: int) -> float:
        """What to wait before ``attempt + 1``, honouring the provider's own figure."""
        stated = retry_after(exc)
        wait = stated if stated is not None else self._base * (2 ** (attempt - 1))
        return min(self._max, max(0.0, wait))

    async def run(self, call: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(1, self._attempts + 1):
            try:
                return await call()
            except Exception as exc:
                if not self._retrying(exc, attempt):
                    raise
                await self._sleep(self.delay_for(exc, attempt))
        raise AssertionError("unreachable: the last attempt either returns or raises")

    def run_blocking(self, call: Callable[[], T]) -> T:
        for attempt in range(1, self._attempts + 1):
            try:
                return call()
            except Exception as exc:
                if not self._retrying(exc, attempt):
                    raise
                self._blocking_sleep(self.delay_for(exc, attempt))
        raise AssertionError("unreachable: the last attempt either returns or raises")

    # ── Internals ────────────────────────────────────────────────

    def _retrying(self, exc: BaseException, attempt: int) -> bool:
        disposition = classify(exc)
        if disposition is not Disposition.RETRY:
            logger.debug(
                "%s: not retrying (%s) %s: %s",
                self._label,
                disposition.value,
                type(exc).__name__,
                _brief(exc),
            )
            return False
        if attempt >= self._attempts:
            logger.warning(
                "%s: giving up after %d attempts: %s", self._label, attempt, _brief(exc)
            )
            return False

        logger.info(
            "%s: transient failure, retrying in %.1fs (attempt %d of %d): %s",
            self._label,
            self.delay_for(exc, attempt),
            attempt,
            self._attempts,
            _brief(exc),
        )
        return True


def _brief(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:200]
