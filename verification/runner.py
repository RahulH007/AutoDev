"""Executing generated code.

Code written by a language model is untrusted input, so everything here runs it
through a narrow door: a scrubbed environment with no API keys in it, a working
directory pinned inside the run workspace, a wall-clock timeout, and a cap on how
much output can be captured.

The :class:`Runner` protocol exists so the execution backend can be swapped. Local
development uses :class:`LocalSubprocessRunner`, which is convenient and fast but
offers no real isolation from the host. :class:`DockerRunner` is the placeholder
for the container backend that would be needed to run this anywhere but a
developer's own machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import venv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from core.config import RunnerBackend, get_settings
from core.logging import get_logger
from schema.verification_schema import CommandResult

logger = get_logger(__name__)

IS_WINDOWS = os.name == "nt"

# Only these variables are forwarded to a subprocess. Everything else, including
# every provider API key, stays behind.
_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "LANG",
        "LC_ALL",
        "OS",
    }
)

# Belt and braces: even an allowlisted name is dropped if it looks like a secret.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

_TRUNCATION_NOTICE = "\n\n... [output truncated] ...\n\n"


def scrubbed_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a minimal environment for a subprocess, free of credentials."""
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper not in _ENV_ALLOWLIST:
            continue
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        env[name] = value

    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


@dataclass(frozen=True)
class PreparedEnv:
    """The Python interpreter a service's tests should run under."""

    python: str
    isolated: bool = False
    error: str = ""
    install_output: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@runtime_checkable
class Runner(Protocol):
    """A place to execute generated code."""

    name: str

    def exec(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...

    def prepare_python(self, service_dir: Path, requirements: Path | None = None) -> PreparedEnv: ...


class LocalSubprocessRunner:
    """Runs commands as plain subprocesses on the host.

    Fine for local development. Not a sandbox: generated code executes with the
    same filesystem and network access as the developer running the pipeline.
    """

    name = "local"

    def __init__(
        self,
        *,
        install_deps: bool | None = None,
        timeout_seconds: int | None = None,
        output_cap_bytes: int | None = None,
    ) -> None:
        settings = get_settings()
        self.install_deps = settings.verify_install_deps if install_deps is None else install_deps
        self.timeout_seconds = timeout_seconds or settings.verify_timeout_seconds
        self.output_cap_bytes = output_cap_bytes or settings.verify_output_cap_bytes

    # ── Execution ────────────────────────────────────────────────

    def exec(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        argv = [str(item) for item in argv]
        limit = timeout or self.timeout_seconds
        cwd.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        logger.debug("exec.start", extra={"argv": argv, "cwd": str(cwd)})

        try:
            completed = subprocess.run(  # noqa: S603 - argv is a list, shell is off
                argv,
                cwd=str(cwd),
                env=scrubbed_env(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, _ = self._cap(_as_text(exc.stdout))
            stderr, _ = self._cap(_as_text(exc.stderr))
            logger.warning("exec.timeout", extra={"argv": argv, "timeout": limit})
            return CommandResult(
                argv=argv,
                exit_code=124,
                stdout=stdout,
                stderr=stderr or f"Timed out after {limit}s.",
                duration_seconds=time.perf_counter() - started,
                timed_out=True,
            )
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            return CommandResult(
                argv=argv,
                exit_code=127,
                stderr=f"Could not execute {argv[0]!r}: {exc}",
                duration_seconds=time.perf_counter() - started,
            )

        stdout, cut_out = self._cap(completed.stdout or "")
        stderr, cut_err = self._cap(completed.stderr or "")
        return CommandResult(
            argv=argv,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.perf_counter() - started,
            truncated=cut_out or cut_err,
        )

    def _cap(self, text: str) -> tuple[str, bool]:
        """Keep the head and the tail; the middle of a huge log is rarely the point."""
        if len(text) <= self.output_cap_bytes:
            return text, False
        head = self.output_cap_bytes // 2
        tail = self.output_cap_bytes - head
        return f"{text[:head]}{_TRUNCATION_NOTICE}{text[-tail:]}", True

    # ── Interpreter setup ────────────────────────────────────────

    def prepare_python(self, service_dir: Path, requirements: Path | None = None) -> PreparedEnv:
        """Return an interpreter for a service, building a virtualenv if configured.

        With ``VERIFY_INSTALL_DEPS=false`` this hands back the interpreter running
        the pipeline. That is much faster and is what the test suite uses, but the
        generated code then only sees packages that happen to be installed here.
        """
        if not self.install_deps:
            return PreparedEnv(python=sys.executable, isolated=False)

        venv_dir = service_dir / ".venv"
        python = _venv_python(venv_dir)

        if not python.exists():
            logger.info("venv.create", extra={"path": str(venv_dir)})
            try:
                venv.EnvBuilder(with_pip=True, symlinks=not IS_WINDOWS, clear=True).create(venv_dir)
            except Exception as exc:  # noqa: BLE001 - surfaced as a report, never fatal
                return PreparedEnv(
                    python=sys.executable,
                    error=f"Could not create a virtualenv for {service_dir.name}: {exc}",
                )

        if not python.exists():
            return PreparedEnv(
                python=sys.executable,
                error=f"Virtualenv at {venv_dir} has no interpreter.",
            )

        install = self._install(python, service_dir, requirements)
        return PreparedEnv(
            python=str(python),
            isolated=True,
            error=install.error,
            install_output=install.install_output,
        )

    def _install(self, python: Path, service_dir: Path, requirements: Path | None) -> PreparedEnv:
        logs: list[str] = []

        if requirements and requirements.is_file():
            result = self.exec(
                [str(python), "-m", "pip", "install", "-r", str(requirements)],
                cwd=service_dir,
                timeout=self.timeout_seconds,
            )
            logs.append(result.output)
            if not result.ok:
                # Bad dependency pins are the model's mistake, so report rather
                # than raise; the developer agent gets told and can fix them.
                return PreparedEnv(
                    python=str(python),
                    isolated=True,
                    error=f"Dependency install failed ({result.summary()}).",
                    install_output="\n".join(logs),
                )

        # The test harness itself, regardless of what the model asked for.
        harness = self.exec(
            [str(python), "-m", "pip", "install", "pytest", "httpx"],
            cwd=service_dir,
            timeout=self.timeout_seconds,
        )
        logs.append(harness.output)
        if not harness.ok:
            return PreparedEnv(
                python=str(python),
                isolated=True,
                error=f"Could not install the test harness ({harness.summary()}).",
                install_output="\n".join(logs),
            )

        return PreparedEnv(python=str(python), isolated=True, install_output="\n".join(logs))


class DockerRunner:
    """Placeholder for container-backed execution.

    Running model-written code with no isolation is acceptable on a laptop and
    nowhere else. This is the seam where a container backend would go; it is
    deliberately not implemented rather than faked, so selecting it fails loudly.
    """

    name = "docker"

    def __init__(self, *_: object, **__: object) -> None:
        raise NotImplementedError(
            "The docker runner backend is not implemented. "
            "Set RUNNER_BACKEND=local to run verification on this machine."
        )

    def exec(self, *_: object, **__: object) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    def prepare_python(self, *_: object, **__: object) -> PreparedEnv:  # pragma: no cover
        raise NotImplementedError


def get_runner(backend: RunnerBackend | None = None, **kwargs: object) -> Runner:
    """Build the runner named by configuration."""
    selected = backend or get_settings().runner_backend
    if selected is RunnerBackend.DOCKER:
        return DockerRunner()  # type: ignore[return-value]
    return LocalSubprocessRunner(**kwargs)  # type: ignore[arg-type]


def _venv_python(venv_dir: Path) -> Path:
    if IS_WINDOWS:
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
