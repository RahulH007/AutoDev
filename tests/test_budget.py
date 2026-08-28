"""The client-side token budget.

A fake clock and a recording sleep keep these deterministic: nothing here waits
in real time, but every wait the budget decides on is asserted exactly.
"""

from __future__ import annotations

import asyncio

import pytest

from core.config import LLMProvider
from llm.budget import (
    BudgetExceededError,
    TokenBudget,
    budget_for,
    estimate_tokens,
    reset_budgets,
)


class FakeClock:
    """A monotonic clock that only moves when told to, or when a sleep is awaited."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def sleep_blocking(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_budgets()
    yield
    reset_budgets()


def make_budget(clock: FakeClock, limit: int = 12_000) -> TokenBudget:
    return TokenBudget(
        limit,
        window_seconds=60.0,
        clock=clock.time,
        sleep=clock.sleep,
        blocking_sleep=clock.sleep_blocking,
    )


class TestReserving:
    async def test_a_call_that_fits_does_not_wait(self, clock: FakeClock):
        budget = make_budget(clock)

        await budget.reserve(6_034)

        assert clock.slept == []
        assert budget.used() == 6_034

    async def test_calls_accumulate_within_the_window(self, clock: FakeClock):
        budget = make_budget(clock)

        await budget.reserve(5_000)
        clock.advance(10)
        await budget.reserve(5_000)

        assert clock.slept == []
        assert budget.used() == 10_000

    async def test_a_call_that_does_not_fit_waits_for_the_oldest_tokens_to_expire(
        self, clock: FakeClock
    ):
        budget = make_budget(clock)
        await budget.reserve(5_000)  # expires at t=60
        clock.advance(30)
        await budget.reserve(5_000)  # expires at t=90

        # 10,000 already held; another 5,000 would reach 15,000 against a 12,000
        # ceiling. Dropping only the first reservation brings it back under, so the
        # wait is until that one ages out at t=60 -- not a blanket minute.
        await budget.reserve(5_000)

        assert clock.slept == [30.0]
        assert budget.used() == 10_000

    async def test_the_real_failure_from_the_run_log_now_waits_instead_of_raising(
        self, clock: FakeClock
    ):
        """Groq reported: Limit 12000, Used 9380, Requested 6034."""
        budget = make_budget(clock, limit=12_000)
        await budget.reserve(9_380)

        await budget.reserve(6_034)

        assert clock.slept == [60.0]
        assert budget.used() == 6_034


class TestWindowExpiry:
    def test_tokens_older_than_the_window_stop_counting(self, clock: FakeClock):
        budget = make_budget(clock)
        budget.record_now(8_000)

        clock.advance(61)

        assert budget.used() == 0

    def test_tokens_inside_the_window_still_count(self, clock: FakeClock):
        budget = make_budget(clock)
        budget.record_now(8_000)

        clock.advance(59)

        assert budget.used() == 8_000


class TestSettling:
    async def test_settling_with_real_usage_frees_the_over_reservation(self, clock: FakeClock):
        budget = make_budget(clock)

        reservation = await budget.reserve(6_000)
        reservation.settle(1_200)

        assert budget.used() == 1_200

    async def test_settling_higher_than_estimated_is_charged_in_full(self, clock: FakeClock):
        budget = make_budget(clock)

        reservation = await budget.reserve(2_000)
        reservation.settle(3_500)

        assert budget.used() == 3_500

    async def test_headroom_freed_by_settling_lets_the_next_call_through(self, clock: FakeClock):
        budget = make_budget(clock)
        reservation = await budget.reserve(11_000)
        reservation.settle(500)

        await budget.reserve(11_000)

        assert clock.slept == []


class TestImpossibleCalls:
    async def test_a_call_larger_than_the_whole_budget_is_refused(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)

        with pytest.raises(BudgetExceededError) as excinfo:
            await budget.reserve(20_000)

        assert "20000" in str(excinfo.value)
        assert "12000" in str(excinfo.value)

    async def test_an_impossible_call_never_sleeps(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)

        with pytest.raises(BudgetExceededError):
            await budget.reserve(20_000)

        assert clock.slept == []


class TestConcurrency:
    async def test_two_agents_cannot_both_reserve_the_same_headroom(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)

        await asyncio.gather(budget.reserve(8_000), budget.reserve(8_000))

        # Without serialisation both would see an empty window and take 16,000.
        assert clock.slept == [60.0]
        assert budget.used() == 8_000


class TestSharedRegistry:
    def test_the_same_provider_and_model_share_one_budget(self):
        first = budget_for(LLMProvider.GROQ, "llama-3.3-70b-versatile", 12_000)
        second = budget_for(LLMProvider.GROQ, "llama-3.3-70b-versatile", 12_000)

        assert first is second

    def test_different_models_get_their_own_budget(self):
        heavy = budget_for(LLMProvider.GROQ, "llama-3.3-70b-versatile", 12_000)
        cheap = budget_for(LLMProvider.GROQ, "llama-3.1-8b-instant", 12_000)

        assert heavy is not cheap

    def test_every_agent_on_one_model_shares_the_organisation_ceiling(self):
        """Groq meters per organisation, so per-agent budgets would overcommit."""
        developer = budget_for(LLMProvider.GROQ, "llama-3.3-70b-versatile", 12_000)
        qa = budget_for(LLMProvider.GROQ, "llama-3.3-70b-versatile", 12_000)

        developer.record_now(12_000)

        assert qa.used() == 12_000


class TestEstimation:
    def test_longer_prompts_estimate_more_tokens(self):
        assert estimate_tokens("x" * 4_000) > estimate_tokens("x" * 400)

    def test_an_empty_prompt_still_reserves_something(self):
        assert estimate_tokens("") > 0


class TestBlockingReserve:
    """The synchronous entry point has to pace itself the same way."""

    def test_a_call_that_fits_does_not_wait(self, clock: FakeClock):
        budget = make_budget(clock)

        budget.reserve_blocking(6_000)

        assert clock.slept == []
        assert budget.used() == 6_000

    def test_a_call_that_does_not_fit_waits_for_room(self, clock: FakeClock):
        budget = make_budget(clock)
        budget.reserve_blocking(9_380)

        budget.reserve_blocking(6_034)

        assert clock.slept == [60.0]
        assert budget.used() == 6_034

    def test_a_call_larger_than_the_whole_budget_is_refused(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)

        with pytest.raises(BudgetExceededError):
            budget.reserve_blocking(20_000)

        assert clock.slept == []

    def test_settling_works_the_same_as_the_async_path(self, clock: FakeClock):
        budget = make_budget(clock)

        reservation = budget.reserve_blocking(6_000)
        reservation.settle(900)

        assert budget.used() == 900


class TestLearningTheRealCeiling:
    """The configured ceiling can simply be wrong for the model in use.

    In the live run the pipeline paced against 12,000 while Groq enforced 8,000,
    so it was rejected while its own window still showed room. The provider says
    which is true in the body of the rejection; the budget should believe it.
    """

    def test_adopting_a_lower_ceiling_takes_effect_immediately(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)

        budget.adopt_limit(8_000)

        assert budget.limit == 8_000

    def test_a_call_that_fitted_the_old_ceiling_now_waits(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)
        await_free = budget.record_now(6_000)
        assert await_free is not None

        budget.adopt_limit(8_000)

        # 6,000 held against 8,000 leaves room for 2,000, not 6,000.
        assert budget.remaining() == 2_000

    def test_a_higher_ceiling_is_adopted_too(self, clock: FakeClock):
        """The provider is authoritative about its own limit in both directions."""
        budget = make_budget(clock, limit=8_000)

        budget.adopt_limit(30_000)

        assert budget.limit == 30_000

    def test_a_nonsense_ceiling_is_ignored(self, clock: FakeClock):
        budget = make_budget(clock, limit=12_000)

        budget.adopt_limit(0)

        assert budget.limit == 12_000

    async def test_resyncing_replaces_a_drifted_estimate_with_the_truth(
        self, clock: FakeClock
    ):
        budget = make_budget(clock, limit=8_000)
        await budget.reserve(7_500)  # our estimate, which had drifted high

        budget.resync(5_276)  # what the provider says it is actually holding

        assert budget.used() == 5_276

    async def test_a_resync_frees_room_for_the_next_call(self, clock: FakeClock):
        budget = make_budget(clock, limit=8_000)
        await budget.reserve(7_500)

        budget.resync(2_000)
        await budget.reserve(5_000)

        assert clock.slept == []

    def test_resynced_tokens_still_expire_with_the_window(self, clock: FakeClock):
        budget = make_budget(clock, limit=8_000)
        budget.resync(5_276)

        clock.advance(61)

        assert budget.used() == 0
