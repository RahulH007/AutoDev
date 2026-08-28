"""Path containment is a security boundary: generated file paths are model output."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.paths import RunWorkspace, UnsafePathError, new_run_id, safe_join, slugify


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Backend API", "backend-api"),
            ("backend-api", "backend-api"),
            ("Frontend UI", "frontend-ui"),
            ("frontend_web", "frontend-web"),
            ("  Spaced  Out  ", "spaced-out"),
            ("API/v1 Service", "api-v1-service"),
            ("MiXeD CaSe", "mixed-case"),
        ],
    )
    def test_normalises_service_names(self, raw: str, expected: str):
        assert slugify(raw) == expected

    def test_variants_of_the_same_name_collapse(self):
        # This is the drift that produced both "Frontend UI/" and "frontend-web/".
        assert slugify("Backend API") == slugify("backend api") == slugify("BACKEND-API")

    def test_falls_back_when_nothing_survives(self):
        assert slugify("!!!") == "service"
        assert slugify("", fallback="unnamed") == "unnamed"


class TestSafeJoin:
    def test_allows_nested_relative_paths(self, tmp_path: Path):
        result = safe_join(tmp_path, "app/api/v1/auth.py")
        assert result == (tmp_path / "app" / "api" / "v1" / "auth.py").resolve()

    def test_allows_multiple_segments(self, tmp_path: Path):
        assert safe_join(tmp_path, "app", "main.py") == (tmp_path / "app" / "main.py").resolve()

    @pytest.mark.parametrize(
        "hostile",
        [
            "../escaped.py",
            "../../escaped.py",
            "../../../../../../etc/passwd",
            "app/../../escaped.py",
            "..\\..\\escaped.py",
            "app/sub/../../../escaped.py",
        ],
    )
    def test_traversal_is_refused(self, tmp_path: Path, hostile: str):
        base = tmp_path / "workspace"
        base.mkdir()
        with pytest.raises(UnsafePathError):
            safe_join(base, hostile)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # A leading slash is a common model quirk meaning "service root".
            ("/app/main.py", "app/main.py"),
            ("/etc/passwd", "etc/passwd"),
            ("//etc/passwd", "etc/passwd"),
            ("C:/Windows/System32/evil.dll", "Windows/System32/evil.dll"),
            ("C:\\Windows\\System32\\evil.dll", "Windows/System32/evil.dll"),
            ("app\\api\\auth.py", "app/api/auth.py"),
        ],
    )
    def test_absolute_and_drive_paths_are_rewritten_as_contained(
        self, tmp_path: Path, raw: str, expected: str
    ):
        """The guarantee is containment, not rejection.

        An absolute path is reinterpreted relative to the workspace, so it can
        never touch the real /etc or C:\\Windows.
        """
        base = tmp_path / "workspace"
        base.mkdir()
        result = safe_join(base, raw)

        assert result == (base / Path(*expected.split("/"))).resolve()
        assert base.resolve() in result.parents

    def test_rejects_empty_and_directory_only(self, tmp_path: Path):
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path)
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "   ")


class TestRunWorkspace:
    def test_create_makes_the_full_layout(self, tmp_path: Path):
        ws = RunWorkspace.create("run1", runs_dir=tmp_path)
        for path in (ws.root, ws.artifacts, ws.source, ws.tests, ws.meta):
            assert path.is_dir()
        assert ws.root == (tmp_path / "run1").resolve()

    def test_runs_are_isolated_from_each_other(self, tmp_path: Path):
        first = RunWorkspace.create("run1", runs_dir=tmp_path)
        second = RunWorkspace.create("run2", runs_dir=tmp_path)

        first.write_source_file("Backend API", "app/main.py", "print('first')")
        second.write_source_file("Backend API", "app/main.py", "print('second')")

        assert first.read_source_file("Backend API", "app/main.py") == "print('first')"
        assert second.read_source_file("Backend API", "app/main.py") == "print('second')"

    def test_service_directories_are_slugged(self, tmp_path: Path):
        ws = RunWorkspace.create("run1", runs_dir=tmp_path)
        written = ws.write_source_file("Backend API", "app/main.py", "x = 1")

        assert written.parent.parent.name == "backend-api"
        # The same service spelled differently lands in the same directory.
        ws.write_source_file("backend api", "app/other.py", "y = 2")
        assert sorted(p.name for p in (ws.source / "backend-api" / "app").iterdir()) == [
            "main.py",
            "other.py",
        ]

    def test_write_creates_intermediate_directories(self, tmp_path: Path):
        ws = RunWorkspace.create("run1", runs_dir=tmp_path)
        target = ws.write_source_file("svc", "a/b/c/deep.py", "pass")
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "pass"

    def test_write_refuses_traversal(self, tmp_path: Path):
        ws = RunWorkspace.create("run1", runs_dir=tmp_path)
        with pytest.raises(UnsafePathError):
            ws.write_source_file("svc", "../../../../escaped.py", "pwned")
        assert not (tmp_path.parent / "escaped.py").exists()

    def test_iter_source_files_skips_the_runner_virtualenv(self, tmp_path: Path):
        ws = RunWorkspace.create("run1", runs_dir=tmp_path)
        ws.write_source_file("svc", "app/main.py", "x = 1")

        venv_file = ws.source / "svc" / ".venv" / "lib" / "junk.py"
        venv_file.parent.mkdir(parents=True)
        venv_file.write_text("noise", encoding="utf-8")

        found = {ws.relative(p) for p in ws.iter_source_files()}
        assert found == {"source/svc/app/main.py"}

    def test_relative_is_posix_style(self, tmp_path: Path):
        ws = RunWorkspace.create("run1", runs_dir=tmp_path)
        target = ws.write_source_file("svc", "app/main.py", "x = 1")
        assert ws.relative(target) == "source/svc/app/main.py"

    def test_for_run_does_not_create_anything(self, tmp_path: Path):
        ws = RunWorkspace.for_run("ghost", runs_dir=tmp_path)
        assert not ws.exists()

    def test_uses_configured_runs_dir_by_default(self, isolated_env: Path):
        ws = RunWorkspace.create("run1")
        assert ws.root == (isolated_env / "runs" / "run1").resolve()


def test_new_run_id_is_unique_and_filesystem_safe():
    ids = {new_run_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(rid.isalnum() for rid in ids)
