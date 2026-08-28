"""Contracts for the verification layer.

These are the only structures that carry evidence about generated code actually
compiling and running. They are what turns the retry loop from an argument
between two language models into a response to facts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    """The outcome of one subprocess invocation."""

    argv: list[str] = Field(default_factory=list)
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part.strip())

    def summary(self) -> str:
        if self.timed_out:
            return f"timed out after {self.duration_seconds:.0f}s: {' '.join(self.argv)}"
        return f"exit {self.exit_code} in {self.duration_seconds:.1f}s: {' '.join(self.argv)}"


class StaticCheck(BaseModel):
    """One static analysis pass over one service."""

    name: str  # compile | pyflakes | json | typescript
    service: str
    passed: bool = True
    skipped: bool = False
    skip_reason: str = ""
    failures: list[str] = Field(default_factory=list)


class StaticReport(BaseModel):
    passed: bool = True
    ran: bool = False
    checks: list[StaticCheck] = Field(default_factory=list)
    # Flattened, prompt-ready lines such as
    # "backend-api/app/main.py:12 SyntaxError: invalid syntax"
    failures: list[str] = Field(default_factory=list)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def summary(self) -> str:
        if not self.ran:
            return "static analysis did not run"
        if self.passed:
            return f"static analysis passed ({len(self.checks)} check(s))"
        return f"static analysis found {len(self.failures)} problem(s)"


class TestFailure(BaseModel):
    test: str
    file: str = ""
    message: str = ""


class ServiceTestResult(BaseModel):
    service: str
    ran: bool = False
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = Field(default_factory=list)
    # A problem with the runner itself (dependency install failed, pytest missing),
    # as opposed to a test that legitimately failed.
    error: str = ""
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.ran and self.failed == 0 and self.errors == 0 and not self.error


class VerificationReport(BaseModel):
    passed: bool = True
    ran: bool = False
    services: list[ServiceTestResult] = Field(default_factory=list)
    summary: str = ""

    @property
    def total_passed(self) -> int:
        return sum(service.passed for service in self.services)

    @property
    def total_failed(self) -> int:
        return sum(service.failed + service.errors for service in self.services)

    def build_summary(self) -> str:
        blocked = [service for service in self.services if service.error]
        blocked_note = f"{len(blocked)} service(s) could not run tests" if blocked else ""

        if not self.ran:
            return blocked_note or "no tests were executed"

        parts = [f"{self.total_passed} passed"]
        if self.total_failed:
            parts.append(f"{self.total_failed} failed")
        if blocked_note:
            parts.append(blocked_note)
        return ", ".join(parts)
