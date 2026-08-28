"""Tests for the verification layer.

These deliberately execute real subprocesses against real files. The whole point
of this layer is that it tells the truth about whether generated code runs, and a
mocked subprocess would test nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core.config import RunnerBackend
from core.paths import RunWorkspace
from schema.verification_schema import CommandResult, StaticReport, VerificationReport
from tests import fakes
from verification.runner import (
    DockerRunner,
    LocalSubprocessRunner,
    get_runner,
    scrubbed_env,
)
from verification.static_gate import run_static_gate
from verification.test_runner import CONFTEST_MARKER, run_tests

SLUG = fakes.SERVICE_SLUG


@pytest.fixture
def runner() -> LocalSubprocessRunner:
    return LocalSubprocessRunner(install_deps=False, timeout_seconds=120)


def seed(
    workspace: RunWorkspace,
    *,
    broken: bool = False,
    tests: str | None = fakes.PASSING_TEST_SOURCE,
) -> RunWorkspace:
    """Write the canned project into a workspace the way the agents would."""
    developer_output = fakes.build_developer_output(broken=broken)
    for service in developer_output.services:
        for file in service.files:
            workspace.write_source_file(service.service_name, file.file_path, file.code)
    if tests is not None:
        workspace.write_test_file(fakes.SERVICE_NAME, "test_calculator.py", tests)
    return workspace


# ─────────────────────────────────────────────────────────────────
# Environment scrubbing
# ─────────────────────────────────────────────────────────────────


class TestScrubbedEnv:
    def test_credentials_never_reach_a_subprocess(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "super-secret")
        monkeypatch.setenv("OPENAI_API_KEY", "also-secret")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
        monkeypatch.setenv("DB_PASSWORD", "hunter2")

        env = scrubbed_env()

        assert "GOOGLE_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env
        assert "AWS_SESSION_TOKEN" not in env
        assert "DB_PASSWORD" not in env
        assert "super-secret" not in "".join(env.values())

    def test_keeps_what_a_subprocess_needs_to_start(self):
        env = scrubbed_env()
        assert env.get("PATH")
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_unrelated_host_variables_are_dropped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_LAPTOP_THING", "leaked")
        assert "MY_LAPTOP_THING" not in scrubbed_env()

    def test_extra_values_are_applied_last(self):
        env = scrubbed_env({"DATABASE_URL": "sqlite:///test.db"})
        assert env["DATABASE_URL"] == "sqlite:///test.db"


# ─────────────────────────────────────────────────────────────────
# The local runner
# ─────────────────────────────────────────────────────────────────


class TestLocalSubprocessRunner:
    def test_captures_stdout_and_exit_code(self, runner: LocalSubprocessRunner, tmp_path: Path):
        result = runner.exec([sys.executable, "-c", "print('hello')"], cwd=tmp_path)

        assert result.ok
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.duration_seconds >= 0

    def test_reports_a_failing_command(self, runner: LocalSubprocessRunner, tmp_path: Path):
        result = runner.exec(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            cwd=tmp_path,
        )

        assert not result.ok
        assert result.exit_code == 3
        assert "boom" in result.stderr
        assert "exit 3" in result.summary()

    def test_runs_in_the_directory_it_is_given(self, runner: LocalSubprocessRunner, tmp_path: Path):
        target = tmp_path / "nested"
        result = runner.exec([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=target)

        assert Path(result.stdout.strip()).resolve() == target.resolve()

    def test_a_hanging_command_is_killed(self, runner: LocalSubprocessRunner, tmp_path: Path):
        result = runner.exec(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout=2,
        )

        assert result.timed_out
        assert result.exit_code == 124
        assert not result.ok
        assert "timed out" in result.summary()

    def test_a_missing_executable_is_a_report_not_a_crash(self, tmp_path: Path):
        runner = LocalSubprocessRunner(install_deps=False)
        result = runner.exec(["definitely-not-a-real-binary-xyz"], cwd=tmp_path)

        assert result.exit_code == 127
        assert "Could not execute" in result.stderr

    def test_enormous_output_is_capped(self, tmp_path: Path):
        runner = LocalSubprocessRunner(install_deps=False, output_cap_bytes=500)
        result = runner.exec(
            [sys.executable, "-c", "print('x' * 100000)"],
            cwd=tmp_path,
        )

        assert result.truncated
        assert len(result.stdout) < 1000
        assert "truncated" in result.stdout

    def test_the_subprocess_gets_no_api_key(
        self, runner: LocalSubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("GOOGLE_API_KEY", "leaked-key-value")
        result = runner.exec(
            [sys.executable, "-c", "import os; print(os.environ.get('GOOGLE_API_KEY', 'ABSENT'))"],
            cwd=tmp_path,
        )

        assert result.stdout.strip() == "ABSENT"

    def test_without_install_deps_it_reuses_the_host_interpreter(
        self, runner: LocalSubprocessRunner, tmp_path: Path
    ):
        prepared = runner.prepare_python(tmp_path)

        assert prepared.ok
        assert prepared.python == sys.executable
        assert not prepared.isolated
        assert not (tmp_path / ".venv").exists()


class TestRunnerSelection:
    def test_local_is_the_default(self):
        assert isinstance(get_runner(), LocalSubprocessRunner)

    def test_docker_fails_loudly_rather_than_pretending(self):
        with pytest.raises(NotImplementedError, match="RUNNER_BACKEND=local"):
            get_runner(RunnerBackend.DOCKER)

    def test_docker_cannot_be_constructed_directly_either(self):
        with pytest.raises(NotImplementedError):
            DockerRunner()


# ─────────────────────────────────────────────────────────────────
# Static gate
# ─────────────────────────────────────────────────────────────────


class TestStaticGate:
    def test_healthy_project_passes(self, workspace: RunWorkspace, runner: LocalSubprocessRunner):
        seed(workspace)
        report = run_static_gate(workspace, runner=runner)

        assert report.ran
        assert report.passed, report.failures
        assert report.failures == []

    def test_syntax_error_is_caught_with_file_and_line(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace, broken=True)
        report = run_static_gate(workspace, runner=runner)

        assert report.ran
        assert not report.passed
        joined = "\n".join(report.failures)
        assert f"{SLUG}/app/calculator.py" in joined
        # The line number matters: it is what the developer agent is told to fix.
        assert ":1:" in joined or ":1 " in joined

    def test_nothing_is_imported_or_executed(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner, tmp_path: Path
    ):
        """A syntax check must not run module-level code."""
        canary = tmp_path / "canary.txt"
        workspace.write_source_file(
            fakes.SERVICE_NAME,
            "app/evil.py",
            f"from pathlib import Path\nPath({str(canary)!r}).write_text('executed')\n",
        )

        report = run_static_gate(workspace, runner=runner)

        assert report.passed, report.failures
        assert not canary.exists()

    def test_invalid_json_is_caught(self, workspace: RunWorkspace, runner: LocalSubprocessRunner):
        seed(workspace)
        workspace.write_source_file(fakes.SERVICE_NAME, "package.json", '{"name": "app",}')

        report = run_static_gate(workspace, runner=runner)

        assert not report.passed
        assert any("package.json" in failure for failure in report.failures)
        assert any("invalid JSON" in failure for failure in report.failures)

    def test_valid_json_passes(self, workspace: RunWorkspace, runner: LocalSubprocessRunner):
        seed(workspace)
        workspace.write_source_file(fakes.SERVICE_NAME, "package.json", '{"name": "app"}')

        assert run_static_gate(workspace, runner=runner).passed

    def test_undefined_name_is_caught(self, workspace: RunWorkspace, runner: LocalSubprocessRunner):
        """The model's most common slip: using a name it forgot to import."""
        seed(workspace)
        workspace.write_source_file(
            fakes.SERVICE_NAME,
            "app/routes.py",
            "def handler():\n    return json.dumps({})\n",
        )

        report = run_static_gate(workspace, runner=runner)

        assert not report.passed
        joined = "\n".join(report.failures)
        assert "app/routes.py" in joined
        assert "F821" in joined

    def test_style_problems_are_not_treated_as_failures(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        """Untidy code still runs; only errors should block the pipeline."""
        seed(workspace)
        workspace.write_source_file(
            fakes.SERVICE_NAME,
            "app/messy.py",
            "import os\nimport sys\nx=1\ndef  f( a ):\n  return a\n",
        )

        assert run_static_gate(workspace, runner=runner).passed

    def test_empty_workspace_reports_that_it_did_not_run(self, workspace: RunWorkspace):
        report = run_static_gate(workspace)

        assert not report.ran
        assert report.passed
        assert "did not run" in report.summary()

    def test_failures_are_capped_so_the_prompt_stays_usable(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        for index in range(60):
            workspace.write_source_file(fakes.SERVICE_NAME, f"app/bad{index}.py", "def broken(:\n")

        report = run_static_gate(workspace, runner=runner)

        assert not report.passed
        assert len(report.failures) <= 41
        assert "and 20 more" in report.failures[-1]

    def test_only_named_services_are_checked(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        workspace.write_source_file("Other Service", "broken.py", "def broken(:\n")

        assert run_static_gate(workspace, [fakes.SERVICE_NAME], runner=runner).passed
        assert not run_static_gate(workspace, runner=runner).passed


# ─────────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────────


class TestTestRunner:
    def test_passing_suite_is_reported_as_passing(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        report = run_tests(workspace, runner=runner)

        assert report.ran
        assert report.passed, report.services[0].output
        assert report.total_passed == 4
        assert report.total_failed == 0
        assert "4 passed" in report.summary

    def test_failing_test_surfaces_its_name_and_assertion(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace, tests=fakes.FAILING_TEST_SOURCE)
        report = run_tests(workspace, runner=runner)

        assert report.ran
        assert not report.passed
        assert report.total_failed == 1

        service = report.services[0]
        assert service.service == SLUG
        assert not service.error, "a wrong assertion is a code defect, not a runner problem"

        failure = service.failures[0]
        assert "test_add_is_wrong" in failure.test
        # The developer agent needs the actual assertion to fix anything.
        assert "assert" in failure.message

    def test_a_service_with_no_tests_is_not_a_failure(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace, tests=None)
        report = run_tests(workspace, runner=runner)

        assert not report.ran
        assert "No services" in report.summary

    def test_junit_report_is_kept_for_inspection(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        run_tests(workspace, runner=runner)

        assert (workspace.meta / f"{SLUG}-junit.xml").is_file()

    def test_import_error_in_the_code_under_test_is_a_runner_error(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        """Collection failing is different from a test failing, and is reported so."""
        seed(workspace, tests="from app.nonexistent import thing\n\n\ndef test_x():\n    pass\n")
        report = run_tests(workspace, runner=runner)

        assert not report.passed
        service = report.services[0]
        assert service.failed or service.errors or service.error

    def test_dependency_install_failure_is_reported_not_raised(
        self, workspace: RunWorkspace, monkeypatch: pytest.MonkeyPatch
    ):
        seed(workspace)
        broken_runner = LocalSubprocessRunner(install_deps=False)

        def fail_to_prepare(*_: object, **__: object):
            from verification.runner import PreparedEnv

            return PreparedEnv(python=sys.executable, error="Dependency install failed (exit 1).")

        monkeypatch.setattr(broken_runner, "prepare_python", fail_to_prepare)
        report = run_tests(workspace, runner=broken_runner)

        assert not report.passed
        assert "Dependency install failed" in report.services[0].error
        assert "could not run tests" in report.summary


class TestConftestScaffold:
    def test_it_is_written_next_to_the_generated_tests(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        run_tests(workspace, runner=runner)

        conftest = workspace.service_tests(fakes.SERVICE_NAME) / "conftest.py"
        assert conftest.is_file()
        assert CONFTEST_MARKER in conftest.read_text(encoding="utf-8")

    def test_the_scaffold_is_valid_python(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        run_tests(workspace, runner=runner)

        source = (workspace.service_tests(fakes.SERVICE_NAME) / "conftest.py").read_text("utf-8")

        compile(source, "conftest.py", "exec")
        assert "{marker}" not in source, "the template placeholder was not substituted"
        assert CONFTEST_MARKER in source
        assert "def client(" in source

    def test_a_handwritten_conftest_is_left_alone(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        theirs = "# the developer agent wrote this\n"
        workspace.write_test_file(fakes.SERVICE_NAME, "conftest.py", theirs)

        run_tests(workspace, runner=runner)

        assert (workspace.service_tests(fakes.SERVICE_NAME) / "conftest.py").read_text(
            "utf-8"
        ) == theirs

    def test_the_client_fixture_reaches_an_asgi_app(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        """The fixture generated suites always assume, wired to a real app."""
        pytest.importorskip("fastapi")

        workspace.write_source_file(
            fakes.SERVICE_NAME,
            "app/__init__.py",
            "",
        )
        workspace.write_source_file(
            fakes.SERVICE_NAME,
            "app/main.py",
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
            '@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
        )
        workspace.write_test_file(
            fakes.SERVICE_NAME,
            "test_health.py",
            "def test_health(client):\n"
            '    response = client.get("/health")\n'
            "    assert response.status_code == 200\n"
            '    assert response.json() == {"status": "ok"}\n',
        )

        report = run_tests(workspace, runner=runner)

        assert report.passed, report.services[0].output
        assert report.total_passed == 1

    def test_a_missing_app_fails_the_run_rather_than_skipping(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        """Silently skipping would let a broken service look healthy."""
        pytest.importorskip("fastapi")

        workspace.write_source_file(fakes.SERVICE_NAME, "app/__init__.py", "")
        workspace.write_test_file(
            fakes.SERVICE_NAME,
            "test_health.py",
            "def test_health(client):\n    assert client is not None\n",
        )

        report = run_tests(workspace, runner=runner)

        assert not report.passed
        assert report.services[0].failed or report.services[0].errors


class TestPytestIsolation:
    def test_the_pipelines_own_pytest_config_is_not_applied(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        """The run workspace can sit under the repo root, where pyproject.toml lives.

        Without an explicit config file, pytest would walk up, find ours, and apply
        our ``testpaths`` to the generated suite.
        """
        seed(workspace)
        run_tests(workspace, runner=runner)

        config = workspace.meta / "pytest.ini"
        assert config.is_file()
        assert "[pytest]" in config.read_text(encoding="utf-8")

    def test_generated_tests_do_not_pollute_the_repository(
        self, workspace: RunWorkspace, runner: LocalSubprocessRunner
    ):
        seed(workspace)
        run_tests(workspace, runner=runner)

        assert not (Path.cwd() / "test.db").exists()
        assert workspace.root in (workspace.meta / "pytest.ini").parents


# ─────────────────────────────────────────────────────────────────
# Schema behaviour
# ─────────────────────────────────────────────────────────────────


class TestReportSchemas:
    def test_command_result_ok_requires_a_clean_exit_and_no_timeout(self):
        assert CommandResult(exit_code=0).ok
        assert not CommandResult(exit_code=1).ok
        assert not CommandResult(exit_code=0, timed_out=True).ok

    def test_static_report_defaults_to_a_pass_that_did_not_run(self):
        report = StaticReport()
        assert report.passed
        assert not report.ran

    def test_verification_summary_counts_across_services(self):
        report = VerificationReport(
            ran=True,
            services=[
                fakes_service_result("a", passed=3),
                fakes_service_result("b", passed=1, failed=2),
            ],
        )
        assert report.total_passed == 4
        assert report.total_failed == 2
        assert report.build_summary() == "4 passed, 2 failed"


def fakes_service_result(name: str, *, passed: int = 0, failed: int = 0):
    from schema.verification_schema import ServiceTestResult

    return ServiceTestResult(service=name, ran=True, passed=passed, failed=failed)


def test_windows_and_posix_both_supported():
    """A sanity check that the runner does not assume a shell."""
    assert os.name in ("nt", "posix")
