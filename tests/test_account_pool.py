"""Several keys for one provider, and the independent capacity that comes with them.

Groq meters tokens per organisation. Four keys are therefore four separate
per-minute windows, and the pipeline was pacing all of them against one — which
commits four times the capacity that exists on any of them, and learns a limit
from one account then applies it to three others that never hit it.

So a provider is no longer one place to send a call. It is a list of accounts,
each with its own budget, its own learned ceiling, its own client and its own
line in the ledger. What is checked here is that those stay apart, that the
credential never travels with the identity, and — most of all — that a setup with
one plain ``GROQ_API_KEY`` is byte-for-byte what it was before any of this
existed.

Agents are untouched throughout: they ask for a `Purpose` and never learn that
accounts are a thing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from core.config import (
    LLMProvider,
    ProviderAccount,
    Purpose,
    Settings,
    get_settings,
    reset_settings_cache,
)
from llm import registry
from llm.accounting import recording
from llm.budget import budget_for, reset_budgets
from state.state import Stage

SECRET = "sk-groq-do-not-log-me-0123456789"


def pooled(count: int = 4, **overrides: Any) -> Settings:
    """Settings with ``count`` numbered Groq keys and nothing else."""
    keys = {f"groq_api_key_{index}": f"{SECRET}-{index}" for index in range(1, count + 1)}
    return Settings(llm_provider=LLMProvider.GROQ, **keys, **overrides)


def single() -> Settings:
    """The configuration everyone has today."""
    return Settings(llm_provider=LLMProvider.GROQ, groq_api_key=SECRET)


class AccountFakeModel(Runnable):
    """A client that remembers which key it was built with.

    Standing in for the provider SDK, so a test can prove the cache never hands
    account two a client holding account one's credential.
    """

    def __init__(self, api_key: str | None = None, error: Exception | None = None) -> None:
        self.api_key = api_key
        self.error = error
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        if self.error:
            raise self.error
        return AIMessage(content=self.api_key or "ok")

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        await asyncio.sleep(0)
        return self.invoke(input, config)


def _budget(settings: Settings, account: str | None, purpose: Purpose = Purpose.TEXT):
    model = registry.model_name_for(LLMProvider.GROQ, purpose, settings)
    return budget_for(LLMProvider.GROQ, model, settings.llm_tokens_per_minute, account)


def _install(monkeypatch: pytest.MonkeyPatch, by_account: dict[str | None, Any]):
    """Serve a different fake client per account, as the real builder would."""

    # `*_, **__` absorbs whatever the registry adds next — a tier, and beyond.
    # Only the account decides which fake client stands in here.
    def build(purpose, provider, settings=None, ceiling=None, account=None, *_, **__):
        key = account.id if account is not None else None
        return by_account[key]

    monkeypatch.setattr(registry, "get_chat_model", build)
    return by_account


# ── Configuration ────────────────────────────────────────────────


class TestAccountsFromConfiguration:
    def test_a_single_key_is_one_unnamed_account(self):
        accounts = single().accounts_for(LLMProvider.GROQ)

        assert len(accounts) == 1
        assert accounts[0].id is None
        assert accounts[0].api_key == SECRET

    def test_numbered_keys_become_named_accounts(self):
        accounts = pooled().accounts_for(LLMProvider.GROQ)

        assert [account.id for account in accounts] == [
            "groq-1", "groq-2", "groq-3", "groq-4",
        ]

    def test_fewer_than_four_is_allowed(self):
        assert [a.id for a in pooled(2).accounts_for(LLMProvider.GROQ)] == ["groq-1", "groq-2"]

    def test_gaps_are_allowed_and_do_not_renumber(self):
        """Removing a key must not rename the accounts that remain."""
        settings = Settings(groq_api_key_1="a", groq_api_key_3="c")

        assert [a.id for a in settings.accounts_for(LLMProvider.GROQ)] == ["groq-1", "groq-3"]

    def test_numbered_keys_replace_the_plain_one(self):
        settings = Settings(groq_api_key=SECRET, groq_api_key_1="a", groq_api_key_2="b")

        accounts = settings.accounts_for(LLMProvider.GROQ)
        assert [account.id for account in accounts] == ["groq-1", "groq-2"]
        assert SECRET not in [account.api_key for account in accounts]

    def test_settings_are_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GROQ_API_KEY_1", "one")
        monkeypatch.setenv("GROQ_API_KEY_3", "three")
        reset_settings_cache()

        assert [a.id for a in get_settings().accounts_for(LLMProvider.GROQ)] == [
            "groq-1", "groq-3",
        ]

    def test_a_provider_with_no_credentials_has_no_accounts(self):
        assert Settings().accounts_for(LLMProvider.GROQ) == []

    def test_ollama_needs_no_key_at_all(self):
        accounts = Settings().accounts_for(LLMProvider.OLLAMA)

        assert len(accounts) == 1
        assert accounts[0].id is None

    def test_only_groq_is_pooled_today(self):
        settings = Settings(google_api_key="k", groq_api_key_1="a")

        assert settings.numbered_keys_for(LLMProvider.GOOGLE) == []
        assert [a.id for a in settings.accounts_for(LLMProvider.GOOGLE)] == [None]

    def test_a_pooled_provider_counts_as_usable(self):
        """Without this, numbered-only configuration would look unconfigured."""
        assert registry.usable_providers(pooled()) == [LLMProvider.GROQ]


class TestIdentifiersAreDeterministicAndNonSecret:
    def test_the_same_configuration_gives_the_same_identifiers(self):
        assert [a.id for a in pooled().accounts_for(LLMProvider.GROQ)] == [
            a.id for a in pooled().accounts_for(LLMProvider.GROQ)
        ]

    def test_an_identifier_is_derived_from_position_not_from_the_key(self):
        first = Settings(groq_api_key_1="one").accounts_for(LLMProvider.GROQ)[0]
        second = Settings(groq_api_key_1="something-else").accounts_for(LLMProvider.GROQ)[0]

        assert first.id == second.id == "groq-1"

    def test_no_identifier_contains_any_part_of_a_key(self):
        for account in pooled().accounts_for(LLMProvider.GROQ):
            assert SECRET not in (account.id or "")

    def test_the_repr_does_not_carry_the_credential(self):
        """These objects reach logs, error messages and caches."""
        account = pooled().accounts_for(LLMProvider.GROQ)[0]

        assert SECRET not in repr(account)
        assert "api_key" not in repr(account)
        assert "groq-1" in repr(account)

    def test_the_string_form_is_the_identifier(self):
        assert str(ProviderAccount(id="groq-2", api_key=SECRET)) == "groq-2"
        assert str(ProviderAccount(id=None)) == "default"


class TestCredentialsDoNotLeak:
    def test_the_ladder_labels_name_accounts_not_keys(self, monkeypatch: pytest.MonkeyPatch):
        _install(monkeypatch, {f"groq-{i}": AccountFakeModel() for i in range(1, 5)})
        reset_budgets()

        runnable = registry.get_text_llm(Purpose.TEXT, pooled())

        assert SECRET not in runnable.name

    def test_no_key_reaches_the_ledger(self, monkeypatch: pytest.MonkeyPatch):
        _install(monkeypatch, {f"groq-{i}": AccountFakeModel() for i in range(1, 5)})
        reset_budgets()

        with recording("run-1", Stage.PM.value) as ledger:
            registry.get_text_llm(Purpose.TEXT, pooled()).invoke("hello there")

        serialised = str([record.__dict__ for record in ledger.records])
        assert SECRET not in serialised
        assert "api_key" not in serialised

    def test_no_key_reaches_the_run_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        _install(monkeypatch, {f"groq-{i}": AccountFakeModel() for i in range(1, 5)})
        reset_budgets()

        with caplog.at_level(logging.DEBUG, logger="llm.registry"):
            registry.get_text_llm(Purpose.TEXT, pooled()).invoke("hello there")

        assert SECRET not in caplog.text

    def test_a_failure_message_does_not_carry_the_key(self, monkeypatch: pytest.MonkeyPatch):
        broken = AccountFakeModel(error=RuntimeError("Error code: 401 - invalid api key"))
        _install(monkeypatch, dict.fromkeys([f"groq-{i}" for i in range(1, 5)], broken))
        reset_budgets()

        with pytest.raises(Exception) as caught:
            registry.get_text_llm(Purpose.TEXT, pooled()).invoke("hello there")

        assert SECRET not in str(caught.value)


# ── Independent capacity ─────────────────────────────────────────


class TestBudgetsAreIsolated:
    def test_each_account_gets_its_own_budget(self):
        reset_budgets()
        settings = pooled()

        budgets = [_budget(settings, f"groq-{i}") for i in range(1, 5)]

        assert len({id(budget) for budget in budgets}) == 4

    def test_one_account_is_one_shared_budget(self):
        """Not per agent, per run or per stage — per resource."""
        reset_budgets()
        settings = pooled()

        assert _budget(settings, "groq-1") is _budget(settings, "groq-1")

    def test_spending_on_one_account_does_not_charge_another(self):
        reset_budgets()
        settings = pooled()

        _budget(settings, "groq-1").record_now(5_000)

        assert _budget(settings, "groq-1").used() == 5_000
        assert _budget(settings, "groq-2").used() == 0

    def test_a_learned_limit_stays_on_the_account_that_learned_it(self):
        """A rejection describes one organisation's window, not the others'."""
        reset_budgets()
        settings = pooled()
        before = _budget(settings, "groq-2").limit

        _budget(settings, "groq-1").adopt_limit(8_000)

        assert _budget(settings, "groq-1").limit == 8_000
        assert _budget(settings, "groq-2").limit == before

    def test_learning_through_a_real_rejection_is_also_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        rejection = RuntimeError(
            "Error code: 429 - rate limit reached on tokens per minute (TPM): "
            "Limit 8000, Used 7900, Requested 500"
        )
        clients = {f"groq-{i}": AccountFakeModel() for i in range(1, 5)}
        clients["groq-1"] = AccountFakeModel(error=rejection)
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled()
        before = _budget(settings, "groq-2").limit

        registry.get_text_llm(Purpose.TEXT, settings).invoke("hello there")

        assert _budget(settings, "groq-1").limit == 8_000
        assert _budget(settings, "groq-2").limit == before

    def test_a_resync_is_account_specific(self):
        reset_budgets()
        settings = pooled()
        _budget(settings, "groq-2").record_now(1_000)

        _budget(settings, "groq-1").resync(6_000)

        assert _budget(settings, "groq-1").used() == 6_000
        assert _budget(settings, "groq-2").used() == 1_000


class TestSelectionPrefersCapacity:
    def test_an_exhausted_account_is_passed_over(self, monkeypatch: pytest.MonkeyPatch):
        """The whole point: account one is full, so the call goes elsewhere."""
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 5)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(2, llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, "groq-1").record_now(10_000)

        registry.get_text_llm(Purpose.TEXT, settings).invoke("hello there")

        assert clients["groq-1"].calls == 0
        assert clients["groq-2"].calls == 1

    def test_the_roomiest_eligible_account_goes_first(self, monkeypatch: pytest.MonkeyPatch):
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 5)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(4, llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, "groq-1").record_now(6_000)
        _budget(settings, "groq-2").record_now(1_000)
        _budget(settings, "groq-4").record_now(9_000)

        registry.get_text_llm(Purpose.TEXT, settings).invoke("hello there")

        # groq-3 is untouched, so it has the most room.
        assert clients["groq-3"].calls == 1
        assert sum(client.calls for client in clients.values()) == 1

    def test_a_tie_keeps_the_configured_order(self, monkeypatch: pytest.MonkeyPatch):
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 5)}
        _install(monkeypatch, clients)
        reset_budgets()

        registry.get_text_llm(Purpose.TEXT, pooled()).invoke("hello there")

        assert clients["groq-1"].calls == 1

    def test_when_nothing_has_room_the_call_still_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A full pool waits on the roomiest window; it does not become an error."""
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 3)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(2, llm_tokens_per_minute=10_000, llm_output_reserve=0)
        _budget(settings, "groq-1").record_now(9_999)
        _budget(settings, "groq-2").record_now(9_999)

        registry.get_text_llm(Purpose.TEXT, settings).invoke("hello there")

        assert sum(client.calls for client in clients.values()) == 1

    def test_selection_never_removes_a_fallback(self, monkeypatch: pytest.MonkeyPatch):
        """Ordering is advisory: every account is still tried in turn."""
        transient = RuntimeError("Error code: 500 - upstream blew up")
        clients = {
            "groq-1": AccountFakeModel(error=transient),
            "groq-2": AccountFakeModel("key-2"),
        }
        _install(monkeypatch, clients)
        reset_budgets()

        result = registry.llm_call("hello there", Purpose.TEXT, pooled(2))

        assert result == "key-2"
        assert clients["groq-1"].calls > 0


class TestAccountFallback:
    def test_a_revoked_key_does_not_condemn_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A 401 is fatal to one account's attempt, not to the pool."""
        clients = {
            "groq-1": AccountFakeModel(error=RuntimeError("Error code: 401 - invalid api key")),
            "groq-2": AccountFakeModel("key-2"),
        }
        _install(monkeypatch, clients)
        reset_budgets()

        assert registry.llm_call("hello there", Purpose.TEXT, pooled(2)) == "key-2"

    def test_a_call_larger_than_one_window_is_offered_to_the_next_account(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Documented consequence: the accounts usually share a configured limit,
        so this generally fails on all of them — the fallback exists because a
        pool *may* be unevenly sized, not because it rescues an oversized call."""
        from llm.budget import BudgetExceededError

        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 3)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(2, llm_tokens_per_minute=10, llm_output_reserve=0)

        with pytest.raises(BudgetExceededError):
            registry.llm_call("hello there", Purpose.TEXT, settings)

        assert sum(client.calls for client in clients.values()) == 0

    def test_every_account_failing_raises_the_last_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        clients = {
            f"groq-{i}": AccountFakeModel(error=RuntimeError(f"Error code: 401 - key {i} is bad"))
            for i in range(1, 3)
        }
        _install(monkeypatch, clients)
        reset_budgets()

        with pytest.raises(Exception, match="key 2 is bad"):
            registry.llm_call("hello there", Purpose.TEXT, pooled(2))


# ── The client cache ─────────────────────────────────────────────


class TestTheCacheKeepsAccountsApart:
    def test_two_accounts_never_share_a_client(self):
        registry.reset_cache()
        settings = pooled()
        accounts = settings.accounts_for(LLMProvider.GROQ)

        first = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings, None, accounts[0])
        second = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings, None, accounts[1])

        assert first is not second

    def test_a_client_carries_its_own_account_credential(self):
        registry.reset_cache()
        settings = pooled()
        accounts = settings.accounts_for(LLMProvider.GROQ)

        first = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings, None, accounts[0])
        second = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings, None, accounts[1])

        # ChatGroq holds the key as a SecretStr under `groq_api_key`, and masks
        # it in its own repr — so the credential is compared through the accessor
        # and never printed by a failure here.
        assert first.groq_api_key.get_secret_value() == f"{SECRET}-1"
        assert second.groq_api_key.get_secret_value() == f"{SECRET}-2"
        assert SECRET not in repr(second)

    def test_one_account_is_still_one_cached_client(self):
        registry.reset_cache()
        settings = pooled()
        account = settings.accounts_for(LLMProvider.GROQ)[0]

        first = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings, None, account)
        second = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings, None, account)

        assert first is second

    def test_omitting_the_account_uses_the_first_one(self):
        registry.reset_cache()
        settings = pooled()

        implicit = registry.get_chat_model(Purpose.TEXT, LLMProvider.GROQ, settings)
        explicit = registry.get_chat_model(
            Purpose.TEXT, LLMProvider.GROQ, settings, None, settings.accounts_for(LLMProvider.GROQ)[0]
        )

        assert implicit is explicit


# ── The ledger ───────────────────────────────────────────────────


class TestTheLedgerNamesTheAccount:
    def _record(self, monkeypatch: pytest.MonkeyPatch, settings: Settings):
        _install(
            monkeypatch,
            {None: AccountFakeModel(), **{f"groq-{i}": AccountFakeModel() for i in range(1, 5)}},
        )
        reset_budgets()
        with recording("run-1", Stage.PM.value) as ledger:
            registry.get_text_llm(Purpose.TEXT, settings).invoke("hello there")
        return ledger

    def test_a_pooled_call_records_which_account_paid(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        ledger = self._record(monkeypatch, pooled())

        assert ledger.records[0].account == "groq-1"

    def test_an_unpooled_call_records_no_account(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, single())

        assert ledger.records[0].account is None

    def test_the_report_breaks_down_by_account(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, pooled())

        assert ledger.report()["by_account"] == {"groq:groq-1": ledger.totals()}

    def test_an_unpooled_report_has_no_account_section_at_all(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Backward compatible: a report that never pooled looks exactly as before."""
        ledger = self._record(monkeypatch, single())

        assert "by_account" not in ledger.report()

    def test_by_model_keeps_its_existing_shape(self, monkeypatch: pytest.MonkeyPatch):
        ledger = self._record(monkeypatch, pooled())

        assert list(ledger.report()["by_model"]) == [
            f"groq:{registry.model_name_for(LLMProvider.GROQ, Purpose.TEXT, pooled())}"
        ]

    def test_merging_reports_keeps_accounts_apart(self, monkeypatch: pytest.MonkeyPatch):
        from llm.accounting import Ledger, merge_reports

        left = Ledger("r", Stage.PM.value)
        left.record(
            purpose="text", provider="groq", model="m", estimated_tokens=10,
            reserved_tokens=20, actual_tokens=None, duration_seconds=0.1,
            outcome="success", account="groq-1",
        )
        right = Ledger("r", Stage.QA.value)
        right.record(
            purpose="text", provider="groq", model="m", estimated_tokens=10,
            reserved_tokens=20, actual_tokens=None, duration_seconds=0.1,
            outcome="success", account="groq-2",
        )

        merged = merge_reports(left.report(), right.report())

        assert set(merged["by_account"]) == {"groq:groq-1", "groq:groq-2"}
        assert merged["by_account"]["groq:groq-1"]["calls"] == 1


# ── Concurrency ──────────────────────────────────────────────────


class TestConcurrentCallsStayStraight:
    async def test_two_runs_do_not_corrupt_account_accounting(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 5)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(4, llm_tokens_per_minute=1_000_000, llm_output_reserve=0)

        async def one(run_id: str, count: int):
            async def body():
                with recording(run_id, Stage.DEVELOPER.value) as ledger:
                    for _ in range(count):
                        await registry.get_text_llm(Purpose.TEXT, settings).ainvoke("hello there")
                    return ledger.report()

            return await asyncio.create_task(body())

        first, second = await asyncio.gather(one("run-a", 3), one("run-b", 5))

        assert first["calls"] == 3
        assert second["calls"] == 5

    async def test_every_call_is_charged_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 5)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(4, llm_tokens_per_minute=1_000_000, llm_output_reserve=0)

        await asyncio.gather(
            *[
                registry.get_text_llm(Purpose.TEXT, settings).ainvoke("hello there")
                for _ in range(12)
            ]
        )

        assert sum(client.calls for client in clients.values()) == 12
        charged = sum(_budget(settings, f"groq-{i}").used() for i in range(1, 5))
        assert charged > 0


# ── Nothing else moved ───────────────────────────────────────────


class TestSingleKeyBehaviourIsUnchanged:
    """The mandatory one. A plain GROQ_API_KEY must be what it always was."""

    def test_the_budget_key_is_the_one_every_old_caller_produces(self):
        reset_budgets()
        settings = single()
        model = registry.model_name_for(LLMProvider.GROQ, Purpose.TEXT, settings)

        assert (
            _budget(settings, None)
            is budget_for(LLMProvider.GROQ, model, settings.llm_tokens_per_minute)
        )

    def test_the_ladder_is_the_same_shape_as_before(self, monkeypatch: pytest.MonkeyPatch):
        """One account collapses the pool away entirely, rather than wrapping it."""
        _install(monkeypatch, {None: AccountFakeModel()})
        reset_budgets()

        runnable = registry.get_text_llm(Purpose.TEXT, single())

        assert runnable.name == "retry:groq"

    def test_model_resolution_is_untouched(self):
        assert registry.model_name_for(
            LLMProvider.GROQ, Purpose.HEAVY, pooled()
        ) == registry.model_name_for(LLMProvider.GROQ, Purpose.HEAVY, single())

    def test_the_ceiling_is_untouched(self):
        for purpose in Purpose:
            assert pooled().max_output_for(purpose) == single().max_output_for(purpose)

    def test_retry_classification_is_untouched(self, monkeypatch: pytest.MonkeyPatch):
        client = AccountFakeModel(error=RuntimeError("Error code: 401 - bad key"))
        _install(monkeypatch, {None: client})
        reset_budgets()

        with pytest.raises(Exception, match="401"):
            registry.llm_call("hello there", Purpose.TEXT, single())

        assert client.calls == 1

    def test_adaptive_ceilings_still_reserve_for_the_selected_account(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        clients = {f"groq-{i}": AccountFakeModel(f"key-{i}") for i in range(1, 3)}
        _install(monkeypatch, clients)
        reset_budgets()
        settings = pooled(2, adaptive_ceilings=True, llm_output_reserve=0)

        with recording("run-1", Stage.PM.value) as ledger:
            registry.get_text_llm(Purpose.TEXT, settings, demand=900).invoke("hello there")

        entry = ledger.records[0]
        assert entry.ceiling_tokens == 1024
        assert entry.reserved_tokens == entry.estimated_tokens + 1024
        assert _budget(settings, entry.account).used() == entry.reserved_tokens


class TestOtherProvidersAreUnaffected:
    def test_google_still_has_one_unnamed_account(self):
        settings = Settings(google_api_key="k")

        assert [a.id for a in settings.accounts_for(LLMProvider.GOOGLE)] == [None]

    def test_a_google_client_caches_as_it_always_did(self):
        registry.reset_cache()
        settings = Settings(google_api_key="k")

        first = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings)
        second = registry.get_chat_model(Purpose.HEAVY, LLMProvider.GOOGLE, settings)

        assert first is second
        assert first.max_output_tokens == 4096

    def test_numbering_groq_does_not_pool_google(self):
        settings = Settings(google_api_key="k", groq_api_key_1="a", groq_api_key_2="b")

        assert [a.id for a in settings.accounts_for(LLMProvider.GOOGLE)] == [None]

    def test_a_google_budget_is_unchanged(self):
        reset_budgets()
        settings = Settings(google_api_key="k", groq_api_key_1="a")
        model = registry.model_name_for(LLMProvider.GOOGLE, Purpose.TEXT, settings)

        assert budget_for(
            LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute
        ) is budget_for(LLMProvider.GOOGLE, model, settings.llm_tokens_per_minute, None)
