"""Run workspaces and path containment.

Two problems are solved here.

First, isolation. Every run gets its own directory tree, so a second run cannot
overwrite the first one's code or leave orphaned files behind from a service the
model happened to name differently this time.

Second, containment. File paths inside generated code come from a language model,
which means they are untrusted input. :func:`safe_join` is the only sanctioned way
to turn one into a real path.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core.config import get_settings

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_DRIVE_PREFIX = re.compile(r"^[a-zA-Z]:")


class UnsafePathError(ValueError):
    """A model-supplied path tried to escape its workspace."""


def new_run_id() -> str:
    return uuid.uuid4().hex


def slugify(name: str, fallback: str = "service") -> str:
    """Normalise a model-chosen name into a stable directory name.

    The architecture and developer agents do not always spell a service the same
    way between runs ("Backend API", "backend-api", "Frontend UI"), which used to
    produce duplicate directories. Slugging both sides makes them agree.
    """
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return slug or fallback


def _normalise_relative(raw: str) -> str:
    """Reduce a model-supplied path to a relative POSIX-style path."""
    candidate = str(raw).replace("\\", "/").strip()
    candidate = _DRIVE_PREFIX.sub("", candidate)
    return candidate.lstrip("/")


def safe_join(base: Path, *parts: str) -> Path:
    """Join under ``base`` and refuse to escape it.

    Guards against absolute paths, Windows drive prefixes, and any amount of
    ``..`` traversal.
    """
    cleaned = [_normalise_relative(part) for part in parts if str(part).strip()]
    if not cleaned:
        raise UnsafePathError("Empty path")

    relative = PurePosixPath(*cleaned)
    if not relative.name:
        raise UnsafePathError(f"Path does not name a file: {'/'.join(cleaned)!r}")

    base_resolved = base.resolve()
    target = (base_resolved / Path(*relative.parts)).resolve()

    if target != base_resolved and base_resolved not in target.parents:
        raise UnsafePathError(f"Path escapes the workspace: {'/'.join(cleaned)!r}")

    return target


@dataclass(frozen=True)
class RunWorkspace:
    """Everything a single pipeline run writes to disk.

    ``runs/<run_id>/artifacts``  PRD, architecture, QA JSON and PDFs
    ``runs/<run_id>/source``     generated source, one directory per service
    ``runs/<run_id>/tests``      generated tests, one directory per service
    ``runs/<run_id>/meta``       verification output such as junit XML
    """

    run_id: str
    root: Path

    @classmethod
    def for_run(cls, run_id: str, runs_dir: Path | None = None) -> RunWorkspace:
        base = Path(runs_dir) if runs_dir is not None else get_settings().runs_dir
        return cls(run_id=run_id, root=(base / run_id).resolve())

    @classmethod
    def create(cls, run_id: str | None = None, runs_dir: Path | None = None) -> RunWorkspace:
        workspace = cls.for_run(run_id or new_run_id(), runs_dir)
        workspace.ensure()
        return workspace

    def ensure(self) -> RunWorkspace:
        for path in (self.root, self.artifacts, self.source, self.tests, self.meta):
            path.mkdir(parents=True, exist_ok=True)
        return self

    # ── Layout ───────────────────────────────────────────────────

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def tests(self) -> Path:
        return self.root / "tests"

    @property
    def meta(self) -> Path:
        return self.root / "meta"

    def service_source(self, service_name: str) -> Path:
        return self.source / slugify(service_name)

    def service_tests(self, service_name: str) -> Path:
        return self.tests / slugify(service_name)

    # ── Writing ──────────────────────────────────────────────────

    def write_source_file(self, service_name: str, file_path: str, content: str) -> Path:
        return self._write(self.service_source(service_name), file_path, content)

    def write_test_file(self, service_name: str, file_path: str, content: str) -> Path:
        return self._write(self.service_tests(service_name), file_path, content)

    def write_shared_source_file(self, file_path: str, content: str) -> Path:
        """For root-level files such as a combined requirements.txt or README."""
        return self._write(self.source, file_path, content)

    def write_artifact(self, file_name: str, content: str) -> Path:
        return self._write(self.artifacts, file_name, content)

    def _write(self, base: Path, file_path: str, content: str) -> Path:
        target = safe_join(base, file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    # ── Reading ──────────────────────────────────────────────────

    def read_source_file(self, service_name: str, file_path: str) -> str:
        return safe_join(self.service_source(service_name), file_path).read_text(encoding="utf-8")

    def iter_source_files(self) -> Iterator[Path]:
        if not self.source.exists():
            return
        for path in sorted(self.source.rglob("*")):
            # Skip the virtualenv the local runner creates inside the workspace.
            if path.is_file() and ".venv" not in path.parts:
                yield path

    def relative(self, path: Path) -> str:
        """Workspace-relative POSIX path, for display and API responses."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def exists(self) -> bool:
        return self.root.is_dir()
