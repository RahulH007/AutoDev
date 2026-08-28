"""The expensive gate: do the generated tests actually pass?

Generated test suites fail for two very different reasons, and the difference
matters to the retry loop. Either the code under test is wrong — which is the
signal we want — or the harness never got off the ground because a dependency
would not install or no ASGI app could be imported. The first becomes a list of
failing tests for the developer agent; the second is recorded as an ``error`` on
the service so nobody mistakes infrastructure trouble for a code defect.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from core.logging import get_logger
from core.paths import RunWorkspace, slugify
from schema.verification_schema import (
    ServiceTestResult,
    TestFailure,
    VerificationReport,
)
from verification.runner import Runner, get_runner

logger = get_logger(__name__)

# pytest's own exit codes.
EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_NO_TESTS = 5

MAX_FAILURES_REPORTED = 15
MAX_FAILURE_CHARS = 1_500

CONFTEST_MARKER = "AgentForge test harness"

_PYTEST_INI = """\
[pytest]
addopts =
testpaths =
python_files = test_*.py *_test.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
"""

_CONFTEST = '''\
"""{marker} — generated so the test suite can find the application.

This file is written by the pipeline, not by the developer agent, and is not
part of the delivered project.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(os.environ.get("AGENTFORGE_SERVICE_ROOT", ".")).resolve()
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

# Sensible defaults so importing the app does not fail on missing configuration.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + (SERVICE_ROOT / "test.db").as_posix())
os.environ.setdefault("SECRET_KEY", "agentforge-test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "agentforge-test-secret")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")

_APP_MODULES = ("app.main", "main", "app", "src.main", "api.main", "server.main")


def _load_app():
    attempts = []
    for name in _APP_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            attempts.append("{{0}}: {{1}}: {{2}}".format(name, type(exc).__name__, exc))
            continue
        instance = getattr(module, "app", None)
        if instance is not None:
            return instance, attempts
        attempts.append("{{0}}: imported but defines no 'app'".format(name))
    return None, attempts


@pytest.fixture(scope="session")
def app():
    instance, attempts = _load_app()
    if instance is None:
        pytest.fail("Could not import an ASGI app. Tried:\\n  " + "\\n  ".join(attempts))
    return instance


@pytest.fixture()
def client(app):
    try:
        from fastapi.testclient import TestClient
    except ImportError:  # pragma: no cover
        from starlette.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
'''


def run_tests(
    workspace: RunWorkspace,
    services: list[str] | None = None,
    runner: Runner | None = None,
) -> VerificationReport:
    """Run every generated test suite in the workspace."""
    report = VerificationReport()
    names = services or _discover_services(workspace)

    if not names:
        report.ran = False
        report.summary = "No services with generated tests were found."
        return report

    runner = runner or get_runner()
    config = _write_pytest_ini(workspace)

    for name in names:
        report.services.append(_run_service(workspace, name, runner, config))

    report.ran = any(service.ran for service in report.services)
    report.passed = all(service.ok for service in report.services if service.ran) and not any(
        service.error for service in report.services
    )
    report.summary = report.build_summary()

    logger.info(
        "verification.done",
        extra={
            "passed": report.passed,
            "tests_passed": report.total_passed,
            "tests_failed": report.total_failed,
        },
    )
    return report


def _run_service(
    workspace: RunWorkspace,
    service: str,
    runner: Runner,
    config: Path,
) -> ServiceTestResult:
    slug = slugify(service)
    result = ServiceTestResult(service=slug)

    source_dir = workspace.service_source(service)
    tests_dir = workspace.service_tests(service)

    if not source_dir.is_dir():
        result.error = f"No generated source directory for {slug}."
        return result
    if not _has_tests(tests_dir):
        result.error = ""
        result.output = f"No test files were generated for {slug}."
        return result

    _scaffold_conftest(tests_dir)

    prepared = runner.prepare_python(source_dir, _requirements_for(workspace, source_dir))
    if not prepared.ok:
        result.error = prepared.error
        result.output = prepared.install_output[-MAX_FAILURE_CHARS:]
        return result

    junit = workspace.meta / f"{slug}-junit.xml"
    junit.unlink(missing_ok=True)

    command = runner.exec(
        [
            prepared.python,
            "-m",
            "pytest",
            str(tests_dir),
            "-c",
            str(config),
            "--rootdir",
            str(source_dir),
            f"--junitxml={junit}",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=source_dir,
        env={
            "PYTHONPATH": str(source_dir),
            "AGENTFORGE_SERVICE_ROOT": str(source_dir),
        },
    )

    result.output = command.output[-MAX_FAILURE_CHARS:]

    if command.timed_out:
        result.error = f"Test run timed out. {command.summary()}"
        return result
    if command.exit_code == EXIT_NO_TESTS:
        result.output = f"pytest collected no tests for {slug}."
        return result
    if command.exit_code not in (EXIT_OK, EXIT_TESTS_FAILED) and not junit.is_file():
        # Collection blew up before any test ran: a bad import, a missing package.
        result.error = f"pytest could not start. {command.summary()}"
        return result

    _fill_from_junit(result, junit)

    if not result.ran and command.exit_code != EXIT_OK:
        result.error = f"pytest reported no results. {command.summary()}"

    return result


# ── junit parsing ────────────────────────────────────────────────


def _fill_from_junit(result: ServiceTestResult, junit: Path) -> None:
    if not junit.is_file():
        return

    try:
        root = ET.parse(junit).getroot()  # noqa: S314 - our own pytest wrote this
    except ET.ParseError as exc:
        result.error = f"Could not read the pytest report: {exc}"
        return

    suites = list(root.iter("testsuite")) or [root]
    for suite in suites:
        for case in suite.iter("testcase"):
            outcome = _classify(case)
            if outcome is None:
                result.passed += 1
                continue

            kind, node = outcome
            if kind == "skipped":
                result.skipped += 1
                continue

            if kind == "failure":
                result.failed += 1
            else:
                result.errors += 1

            if len(result.failures) < MAX_FAILURES_REPORTED:
                result.failures.append(
                    TestFailure(
                        test=_test_id(case),
                        file=case.get("file", ""),
                        message=_failure_text(node),
                    )
                )

    result.ran = bool(result.passed or result.failed or result.errors or result.skipped)


def _classify(case: ET.Element) -> tuple[str, ET.Element] | None:
    for tag in ("failure", "error", "skipped"):
        node = case.find(tag)
        if node is not None:
            return tag, node
    return None


def _test_id(case: ET.Element) -> str:
    classname = case.get("classname", "")
    name = case.get("name", "<unknown>")
    return f"{classname}::{name}" if classname else name


def _failure_text(node: ET.Element) -> str:
    """Keep the tail of a traceback — the assertion is at the bottom."""
    parts = [node.get("message", ""), (node.text or "").strip()]
    text = "\n".join(part for part in parts if part).strip()
    if len(text) <= MAX_FAILURE_CHARS:
        return text
    return "... [truncated] ...\n" + text[-MAX_FAILURE_CHARS:]


# ── Scaffolding and discovery ────────────────────────────────────


def _write_pytest_ini(workspace: RunWorkspace) -> Path:
    """Pin pytest's configuration.

    Without this, pytest walks up from the run workspace and finds the pipeline's
    own ``pyproject.toml``, then applies our ``testpaths`` and plugin settings to
    the generated suite.
    """
    workspace.meta.mkdir(parents=True, exist_ok=True)
    config = workspace.meta / "pytest.ini"
    config.write_text(_PYTEST_INI, encoding="utf-8")
    return config


def _scaffold_conftest(tests_dir: Path) -> Path | None:
    """Provide the ``client`` fixture generated tests almost always assume exists.

    If the developer agent wrote its own ``conftest.py``, that one wins.
    """
    conftest = tests_dir / "conftest.py"
    if conftest.is_file() and CONFTEST_MARKER not in conftest.read_text(encoding="utf-8"):
        return None

    conftest.parent.mkdir(parents=True, exist_ok=True)
    conftest.write_text(_CONFTEST.format(marker=CONFTEST_MARKER), encoding="utf-8")
    return conftest


def _requirements_for(workspace: RunWorkspace, source_dir: Path) -> Path | None:
    for candidate in (source_dir / "requirements.txt", workspace.source / "requirements.txt"):
        if candidate.is_file():
            return candidate
    return None


def _has_tests(tests_dir: Path) -> bool:
    if not tests_dir.is_dir():
        return False
    return any(
        path.is_file() and (path.name.startswith("test_") or path.name.endswith("_test.py"))
        for path in tests_dir.rglob("*.py")
    )


def _discover_services(workspace: RunWorkspace) -> list[str]:
    if not workspace.tests.is_dir():
        return []
    return sorted(path.name for path in workspace.tests.iterdir() if path.is_dir())
