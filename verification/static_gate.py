"""The cheap gate: does the generated code even parse?

This runs before anything is executed and before the QA agent is asked for an
opinion. A syntax error or an undefined name is not a matter of judgement, and
there is no point spending a model call to discover one. Nothing in this module
imports or executes generated code — Python files are checked with :func:`compile`,
which parses without running.
"""

from __future__ import annotations

import json
import re
import sys
from importlib.util import find_spec
from pathlib import Path

from core.logging import get_logger
from core.paths import RunWorkspace
from schema.verification_schema import StaticCheck, StaticReport
from verification.runner import Runner, get_runner

logger = get_logger(__name__)

# Enough for the developer agent to act on without flooding its context window.
MAX_REPORTED_FAILURES = 40

_SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache", "dist"}

# Syntax errors, undefined names, f-string and comparison mistakes. Deliberately
# not style rules: the point is code that cannot work, not code that is untidy.
_RUFF_RULES = "E9,F63,F7,F82"

# ruff --output-format=concise emits "path:line:col: CODE message". Matching that
# shape exactly keeps banners like "All checks passed!" out of the failure list.
_RUFF_DIAGNOSTIC = re.compile(r"^(?P<path>.+?):\d+:\d+: [A-Z]+\d+ ")


def run_static_gate(
    workspace: RunWorkspace,
    services: list[str] | None = None,
    runner: Runner | None = None,
) -> StaticReport:
    """Parse-check every generated file in the workspace."""
    report = StaticReport()
    service_dirs = _service_dirs(workspace, services)

    if not service_dirs:
        report.ran = False
        report.checks.append(
            StaticCheck(
                name="compile",
                service="-",
                skipped=True,
                skip_reason="No generated source directories were found.",
            )
        )
        return report

    runner = runner or get_runner()

    for service_dir in service_dirs:
        service = service_dir.name
        report.checks.append(_check_python_syntax(service, service_dir))
        report.checks.append(_check_json(service, service_dir))
        report.checks.append(_check_undefined_names(service, service_dir, runner))

    report.ran = True
    failures: list[str] = []
    for check in report.checks:
        failures.extend(check.failures)

    report.failures = failures[:MAX_REPORTED_FAILURES]
    if len(failures) > MAX_REPORTED_FAILURES:
        report.failures.append(f"... and {len(failures) - MAX_REPORTED_FAILURES} more")
    report.passed = not failures

    logger.info(
        "static_gate.done",
        extra={"passed": report.passed, "failures": len(failures)},
    )
    return report


# ── Individual checks ────────────────────────────────────────────


def _check_python_syntax(service: str, service_dir: Path) -> StaticCheck:
    check = StaticCheck(name="compile", service=service)
    files = _iter_files(service_dir, ".py")

    if not files:
        check.skipped = True
        check.skip_reason = "No Python files."
        return check

    for path in files:
        rel = path.relative_to(service_dir).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            check.failures.append(f"{service}/{rel}: unreadable ({exc})")
            continue
        try:
            compile(source, str(path), "exec", dont_inherit=True)
        except SyntaxError as exc:
            check.failures.append(f"{service}/{rel}:{exc.lineno or 0}: {exc.msg}")
        except ValueError as exc:
            # Null bytes and similar; compile() rejects these outside SyntaxError.
            check.failures.append(f"{service}/{rel}: {exc}")

    check.passed = not check.failures
    return check


def _check_json(service: str, service_dir: Path) -> StaticCheck:
    check = StaticCheck(name="json", service=service)
    files = _iter_files(service_dir, ".json")

    if not files:
        check.skipped = True
        check.skip_reason = "No JSON files."
        return check

    for path in files:
        rel = path.relative_to(service_dir).as_posix()
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            check.failures.append(f"{service}/{rel}:{exc.lineno}: invalid JSON, {exc.msg}")
        except (OSError, UnicodeDecodeError) as exc:
            check.failures.append(f"{service}/{rel}: unreadable ({exc})")

    check.passed = not check.failures
    return check


def _check_undefined_names(service: str, service_dir: Path, runner: Runner) -> StaticCheck:
    """Catch missing imports and typo'd names, which are the model's usual slip."""
    check = StaticCheck(name="pyflakes", service=service)

    if not _iter_files(service_dir, ".py"):
        check.skipped = True
        check.skip_reason = "No Python files."
        return check

    if find_spec("ruff") is None:
        check.skipped = True
        check.skip_reason = "ruff is not installed."
        return check

    result = runner.exec(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",  # ignore any pyproject.toml above the workspace
            "--no-cache",
            f"--select={_RUFF_RULES}",
            "--output-format=concise",
            ".",
        ],
        cwd=service_dir,
        timeout=120,
    )

    if result.exit_code not in (0, 1):
        check.skipped = True
        check.skip_reason = f"ruff could not run ({result.summary()})."
        return check

    for raw in result.stdout.splitlines():
        line = _strip_dot_prefix(raw.strip())
        match = _RUFF_DIAGNOSTIC.match(line)
        if not match:
            continue
        # ruff reports native separators; the agents deal in POSIX paths.
        path = match.group("path").replace("\\", "/")
        check.failures.append(f"{service}/{path}{line[match.end('path') :]}")

    check.passed = not check.failures
    return check


# ── Helpers ──────────────────────────────────────────────────────


def _service_dirs(workspace: RunWorkspace, services: list[str] | None) -> list[Path]:
    if services:
        candidates = [workspace.service_source(name) for name in services]
    elif workspace.source.is_dir():
        candidates = sorted(p for p in workspace.source.iterdir() if p.is_dir())
    else:
        candidates = []
    return [path for path in candidates if path.is_dir() and path.name not in _SKIP_DIRS]


def _strip_dot_prefix(line: str) -> str:
    for prefix in ("./", ".\\"):
        if line.startswith(prefix):
            return line[len(prefix) :]
    return line


def _iter_files(root: Path, suffix: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(f"*{suffix}")
        if path.is_file() and not _SKIP_DIRS.intersection(path.parts)
    )
