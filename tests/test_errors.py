"""Classifying provider errors, and the retry policy built on that.

The message fixtures below are real: they are what Groq returned during the live
run that this work exists to fix. A classifier tested only against invented
strings proves nothing about the failure it was written for.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from llm.budget import BudgetExceededError
from llm.errors import (
    Disposition,
    Retrier,
    classify,
    reported_usage,
    retry_after,
    status_code,
)

# ── Real messages from run 5c9fc957282046dd8da367935c467d5f ───────

TOOL_CHOICE_400 = (
    "Error code: 400 - {'error': {'message': 'Tool choice is required, but model "
    "did not call a tool', 'type': 'invalid_request_error', 'code': 'tool_use_failed'}}"
)
TOOL_PARSE_400 = (
    "Error code: 400 - {'error': {'message': 'Failed to parse tool call arguments "
    "as JSON', 'type': 'invalid_request_error', 'code': 'tool_use_failed'}}"
)
JSON_VALIDATE_400 = (
    "Error code: 400 - {'error': {'message': 'Failed to generate JSON. Please "
    "adjust your prompt.', 'type': 'invalid_request_error', "
    "'code': 'json_validate_failed'}}"
)
RATE_LIMIT_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "openai/gpt-oss-120b in organization org_abc service tier on_demand on "
    "tokens per minute (TPM): Limit 12000, Used 11630, Requested 2436. Please try "
    "again in 10.33s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)
DECOMMISSIONED_404 = (
    "Error code: 404 - {'error': {'message': 'The model llama-3.3-70b-versatile "
    "has been decommissioned.', 'type': 'invalid_request_error', "
    "'code': 'model_decommissioned'}}"
)
BAD_KEY_401 = (
    "Error code: 401 - {'error': {'message': 'Invalid API Key', "
    "'type': 'invalid_request_error', 'code': 'invalid_api_key'}}"
)
UPSTREAM_503 = (
    "Error code: 503 - {'error': {'message': 'Service Unavailable', "
    "'type': 'internal_server_error'}}"
)


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}


class FakeAPIError(Exception):
    """The shape the openai/groq SDK exceptions present."""

    def __init__(self, message: str, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status_code = status
        self.response = FakeResponse(status, headers)


class Point(BaseModel):
    x: int


# ── Reading the status code out of whatever we were handed ────────


class TestStatusCode:
    def test_reads_a_status_code_attribute(self):
        assert status_code(FakeAPIError("boom", 429)) == 429

    def test_falls_back_to_the_response_object(self):
        exc = Exception("boom")
        exc.response = FakeResponse(503)  # type: ignore[attr-defined]
        assert status_code(exc) == 503

    def test_recovers_the_code_from_the_message_when_there_is_no_attribute(self):
        """LangChain often re-raises a plain Exception carrying only the text."""
        assert status_code(Exception(RATE_LIMIT_429)) == 429
        assert status_code(Exception(TOOL_CHOICE_400)) == 400

    def test_a_plain_error_has_no_status(self):
        assert status_code(ValueError("no JSON object found")) is None

    def test_a_number_in_the_body_is_not_mistaken_for_a_status(self):
        assert status_code(Exception("Limit 12000, Used 9380, Requested 6034")) is None


# ── Classification ────────────────────────────────────────────────


class TestTransientErrors:
    """Worth trying the same call again."""

    def test_rate_limits_are_retried(self):
        assert classify(Exception(RATE_LIMIT_429)) is Disposition.RETRY

    def test_provider_outages_are_retried(self):
        assert classify(Exception(UPSTREAM_503)) is Disposition.RETRY

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
    def test_every_server_error_is_retried(self, status: int):
        assert classify(FakeAPIError("upstream", status)) is Disposition.RETRY

    def test_timeouts_are_retried(self):
        assert classify(TimeoutError("read timed out")) is Disposition.RETRY

    def test_dropped_connections_are_retried(self):
        assert classify(ConnectionError("connection reset by peer")) is Disposition.RETRY

    def test_a_provider_timeout_class_is_recognised_by_name(self):
        """httpx and the provider SDKs do not subclass the builtins."""

        class APITimeoutError(Exception):
            pass

        class APIConnectionError(Exception):
            pass

        assert classify(APITimeoutError("request timed out")) is Disposition.RETRY
        assert classify(APIConnectionError("failed to connect")) is Disposition.RETRY


class TestCapabilityErrors:
    """The model cannot do this. Retrying repeats it; the next rung might not."""

    def test_a_model_that_will_not_call_a_tool_degrades_immediately(self):
        assert classify(Exception(TOOL_CHOICE_400)) is Disposition.DEGRADE

    def test_unparseable_tool_arguments_degrade_immediately(self):
        assert classify(Exception(TOOL_PARSE_400)) is Disposition.DEGRADE

    def test_a_schema_the_model_cannot_satisfy_degrades_immediately(self):
        assert classify(Exception(JSON_VALIDATE_400)) is Disposition.DEGRADE

    def test_any_other_bad_request_degrades(self):
        assert classify(FakeAPIError("malformed request", 400)) is Disposition.DEGRADE

    def test_a_schema_validation_failure_degrades(self):
        with pytest.raises(ValidationError) as excinfo:
            Point(x="not a number")  # type: ignore[arg-type]
        assert classify(excinfo.value) is Disposition.DEGRADE

    def test_a_json_decode_failure_degrades(self):
        with pytest.raises(json.JSONDecodeError) as excinfo:
            json.loads("{not json")
        assert classify(excinfo.value) is Disposition.DEGRADE

    def test_a_response_with_no_json_in_it_degrades(self):
        """What the parse rung raises when the model returned prose."""
        assert classify(ValueError("No JSON object found in response")) is Disposition.DEGRADE


class TestFatalErrors:
    """Nothing further down the ladder will help either."""

    def test_a_call_larger_than_the_budget_is_never_retried(self):
        assert classify(BudgetExceededError("too big")) is Disposition.ABORT

    def test_a_rejected_key_aborts(self):
        assert classify(Exception(BAD_KEY_401)) is Disposition.ABORT

    def test_a_forbidden_response_aborts(self):
        assert classify(FakeAPIError("forbidden", 403)) is Disposition.ABORT

    def test_a_decommissioned_model_aborts(self):
        """Every rung names the same model, so degrading cannot help."""
        assert classify(Exception(DECOMMISSIONED_404)) is Disposition.ABORT


class TestUnknownErrors:
    def test_an_unrecognised_error_degrades_rather_than_burning_attempts(self):
        assert classify(RuntimeError("something new")) is Disposition.DEGRADE


# ── Retry-After ───────────────────────────────────────────────────


class TestRetryAfter:
    def test_a_numeric_header_is_used(self):
        assert retry_after(FakeAPIError("slow down", 429, {"retry-after": "12"})) == 12.0

    def test_a_fractional_header_is_used(self):
        assert retry_after(FakeAPIError("slow down", 429, {"retry-after": "0.5"})) == 0.5

    def test_the_header_is_matched_case_insensitively(self):
        assert retry_after(FakeAPIError("slow down", 429, {"Retry-After": "3"})) == 3.0

    def test_groqs_wait_is_read_out_of_the_message_when_there_is_no_header(self):
        assert retry_after(Exception(RATE_LIMIT_429)) == pytest.approx(10.33)

    def test_a_wait_expressed_in_minutes_and_seconds_is_read(self):
        assert retry_after(Exception("Please try again in 1m30s.")) == pytest.approx(90.0)

    def test_an_error_that_names_no_wait_returns_nothing(self):
        assert retry_after(Exception(TOOL_CHOICE_400)) is None

    def test_a_non_numeric_header_is_ignored_rather_than_crashing(self):
        exc = FakeAPIError("slow down", 429, {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
        assert retry_after(exc) is None


# ── The retry policy ──────────────────────────────────────────────


class RecordingSleep:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)

    def blocking(self, seconds: float) -> None:
        self.waits.append(seconds)


def make_retrier(sleep: RecordingSleep, attempts: int = 3) -> Retrier:
    return Retrier(
        attempts,
        base_delay=2.0,
        max_delay=60.0,
        sleep=sleep,
        blocking_sleep=sleep.blocking,
        label="test",
    )


class Failing:
    """Raises the given errors in order, then returns a value."""

    def __init__(self, *errors: Exception, value: str = "ok") -> None:
        self.errors = list(errors)
        self.value = value
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.value

    async def acall(self) -> str:
        return self.__call__()


class TestRetrier:
    async def test_a_successful_call_is_made_once(self):
        sleep = RecordingSleep()
        call = Failing()

        assert await make_retrier(sleep).run(call.acall) == "ok"
        assert call.calls == 1
        assert sleep.waits == []

    async def test_a_transient_failure_is_retried(self):
        sleep = RecordingSleep()
        call = Failing(Exception(UPSTREAM_503))

        assert await make_retrier(sleep).run(call.acall) == "ok"
        assert call.calls == 2

    async def test_a_deterministic_failure_is_attempted_exactly_once(self):
        """The whole point: three identical 400s taught us nothing and cost budget."""
        sleep = RecordingSleep()
        call = Failing(
            Exception(TOOL_CHOICE_400), Exception(TOOL_CHOICE_400), Exception(TOOL_CHOICE_400)
        )

        with pytest.raises(Exception, match="Tool choice is required"):
            await make_retrier(sleep).run(call.acall)

        assert call.calls == 1
        assert sleep.waits == []

    async def test_a_budget_refusal_is_attempted_exactly_once(self):
        sleep = RecordingSleep()
        call = Failing(BudgetExceededError("too big"))

        with pytest.raises(BudgetExceededError):
            await make_retrier(sleep).run(call.acall)

        assert call.calls == 1

    async def test_attempts_are_capped(self):
        sleep = RecordingSleep()
        call = Failing(*[Exception(UPSTREAM_503) for _ in range(5)])

        with pytest.raises(Exception, match="Service Unavailable"):
            await make_retrier(sleep, attempts=3).run(call.acall)

        assert call.calls == 3
        # Three attempts means two waits: nothing is slept after the last one.
        assert len(sleep.waits) == 2

    async def test_backoff_grows_between_attempts(self):
        sleep = RecordingSleep()
        call = Failing(*[Exception(UPSTREAM_503) for _ in range(5)])

        with pytest.raises(Exception, match="Service Unavailable"):
            await make_retrier(sleep, attempts=4).run(call.acall)

        assert sleep.waits == [2.0, 4.0, 8.0]

    async def test_the_providers_own_wait_beats_our_backoff(self):
        sleep = RecordingSleep()
        call = Failing(Exception(RATE_LIMIT_429))

        await make_retrier(sleep).run(call.acall)

        assert sleep.waits == [pytest.approx(10.33)]

    async def test_a_wait_longer_than_the_cap_is_clamped(self):
        sleep = RecordingSleep()
        call = Failing(Exception("Error code: 429 - Please try again in 3600s."))

        await make_retrier(sleep).run(call.acall)

        assert sleep.waits == [60.0]

    async def test_the_last_error_is_the_one_raised(self):
        sleep = RecordingSleep()
        call = Failing(Exception(UPSTREAM_503), Exception(TOOL_CHOICE_400))

        with pytest.raises(Exception, match="Tool choice is required"):
            await make_retrier(sleep).run(call.acall)

        assert call.calls == 2


class TestRetrierBlocking:
    """The synchronous twin has to follow the same policy."""

    def test_a_deterministic_failure_is_attempted_exactly_once(self):
        sleep = RecordingSleep()
        call = Failing(Exception(JSON_VALIDATE_400), Exception(JSON_VALIDATE_400))

        with pytest.raises(Exception, match="Failed to generate JSON"):
            make_retrier(sleep).run_blocking(call)

        assert call.calls == 1

    def test_a_transient_failure_is_retried(self):
        sleep = RecordingSleep()
        call = Failing(Exception(UPSTREAM_503))

        assert make_retrier(sleep).run_blocking(call) == "ok"
        assert call.calls == 2
        assert sleep.waits == [2.0]


# ── What the provider tells us about its own window ───────────────

# The real body from the run: the ceiling Groq enforces is 8000, not the 12000
# the pipeline had been configured with.
REAL_TPM_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "openai/gpt-oss-120b in organization org_01jcst on_demand on tokens per "
    "minute (TPM): Limit 8000, Used 5276, Requested 5857. Please try again in "
    "23.4975s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)
RPM_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "openai/gpt-oss-120b on requests per minute (RPM): Limit 30, Used 30, "
    "Requested 1. Please try again in 2s.', 'code': 'rate_limit_exceeded'}}"
)


class TestReportedUsage:
    def test_the_real_429_states_a_ceiling_lower_than_the_one_configured(self):
        report = reported_usage(Exception(REAL_TPM_429))

        assert report is not None
        assert report.limit == 8_000
        assert report.used == 5_276
        assert report.requested == 5_857

    def test_a_requests_per_minute_limit_is_not_read_as_a_token_limit(self):
        """Adopting RPM's "Limit 30" as a token ceiling would stall every call."""
        assert reported_usage(Exception(RPM_429)) is None

    def test_an_error_that_states_no_figures_reports_nothing(self):
        assert reported_usage(Exception(TOOL_CHOICE_400)) is None
        assert reported_usage(Exception(UPSTREAM_503)) is None
