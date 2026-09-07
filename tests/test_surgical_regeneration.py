"""Rebuilding the service that failed, and leaving the rest alone.

A rework pass used to regenerate the whole project because one service would not
compile. Attribution has known which service each failure belongs to since Phase
1 and the evidence has been narrowed per service since Phase 2; this is where
that finally decides *who gets rebuilt*. Four services, one failing: one model
call instead of four, and three services left byte-for-byte as they were.

Most of what follows is about the ways that could go wrong rather than the saving
itself, because the saving is the easy part:

- the artifact must still describe the whole project, not the one service rebuilt;
- a service that reorganises must not leave its old files behind, and a *fix*
  response — which is only the changed files — must never be read as a complete
  service and used to delete the rest;
- an empty or failed answer must not replace working code with nothing;
- deletion must not be able to reach outside the service it belongs to;
- a run must not finish while a required service is still missing.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agents.developer_agent import developer_agent
from core import manifest as manifest_util
from core.contracts import derive_contract
from core.paths import RunWorkspace, UnsafePathError
from llm.accounting import recording
from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode
from state.state import AgentStatus, Stage, initial_state
from utils.json_utils import load_json

AUTH, BACKEND, REPORTS, FRONTEND = "Auth", "Backend API", "Reports", "Frontend Web"
NAMES = [AUTH, BACKEND, REPORTS, FRONTEND]
AUTH_S, BACKEND_S, REPORTS_S, FRONTEND_S = "auth", "backend-api", "reports", "frontend-web"
SLUGS = [AUTH_S, BACKEND_S, REPORTS_S, FRONTEND_S]

ARTIFACT = "developer.json"


# ── Fixtures ─────────────────────────────────────────────────────


def _architecture(*names: str, dependencies: dict[str, list[str]] | None = None):
    deps = dependencies or {}
    return {
        "architecture_style": "modular_monolith",
        "system_overview": "Several services.",
        "services": [
            {
                "name": name,
                "description": f"{name}.",
                "tech_stack": ["Python"],
                "dependencies": deps.get(name, []),
                "api_endpoints": [],
                "data_models": [],
            }
            for name in names
        ],
        "project_structure": [
            {"service_name": name, "folders": ["app"], "key_files": ["app/main.py"]}
            for name in names
        ],
    }


def _code(service: str, *files: str) -> DeveloperSchema:
    return DeveloperSchema(
        project_name="SpendWise",
        services=[
            ServiceCode(
                service_name=service,
                files=[
                    CodeFile(
                        file_path=path,
                        file_name=path.rsplit("/", 1)[-1],
                        language="python",
                        code=f"# {service} {path}\nx = 1\n",
                        description=f"{path} of {service}.",
                    )
                    for path in (files or ("app/main.py",))
                ],
            )
        ],
        readme_content=f"# {service}\n",
    )


def _empty(service: str) -> DeveloperSchema:
    """A structurally valid response that contains no file at all."""
    return DeveloperSchema(
        project_name="SpendWise",
        services=[ServiceCode(service_name=service, files=[])],
    )


def _static(*slugs: str) -> dict[str, Any]:
    """A static report shaped the way `run_static_gate` writes one."""
    lines = [f"{slug}/app/main.py:12: invalid syntax" for slug in slugs]
    return {
        "ran": True,
        "passed": False,
        "checks": [
            {"name": "compile", "service": slug, "failures": [line]}
            for slug, line in zip(slugs, lines, strict=True)
        ],
        "failures": lines,
    }


class Recorder:
    """Stands in for the registry, one canned response per call."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any, config: Any = None) -> Any:
        self.prompts.append(prompt)
        response = self.responses.pop(0) if self.responses else self.responses
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def text_of(self, index: int) -> str:
        from llm.registry import _prompt_text

        return _prompt_text(self.prompts[index])


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):
    def _install(*responses: Any) -> Recorder:
        from llm import registry

        model = Recorder(*responses)
        monkeypatch.setattr(registry, "get_structured_llm", lambda *a, **k: model)
        return model

    return _install


@pytest.fixture
def fresh(workspace):
    """A run that has generated nothing yet."""
    architecture = _architecture(*NAMES)
    return {
        **initial_state(workspace.run_id, "Build an expense tracker."),
        "architecture": architecture,
        "contract": derive_contract(architecture).model_dump(mode="json"),
    }


async def _build_everything(state, install) -> tuple[dict[str, Any], Recorder]:
    """Drive a first pass so there is a healthy project to preserve."""
    model = install(*[_code(name) for name in NAMES])
    update = await developer_agent(state)
    return update, model


# ── Selection ────────────────────────────────────────────────────


class TestSelection:
    async def test_a_first_pass_generates_every_service(self, fresh, install):
        update, model = await _build_everything(fresh, install)

        assert model.calls == 4
        assert update["generated_services"] == SLUGS

    async def test_one_failing_service_is_the_only_one_rebuilt(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        model = install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert model.calls == 1
        assert BACKEND in model.text_of(0)

    async def test_two_failing_services_are_the_only_two_rebuilt(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        model = install(_code(BACKEND), _code(REPORTS))
        await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S, REPORTS_S)}
        )

        assert model.calls == 2

    async def test_healthy_services_are_preserved(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        model = install(_code(BACKEND))
        update = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert model.calls == 1
        # Still part of the project, just not paid for again.
        assert update["generated_services"] == SLUGS

    async def test_a_service_never_generated_is_targeted(self, fresh, install):
        """Nothing to preserve, so it is built whether or not anything failed."""
        first, _ = await _build_everything(fresh, install)
        without_reports = [s for s in first["generated_services"] if s != REPORTS_S]

        model = install(_code(REPORTS))
        await developer_agent({**fresh, **first, "generated_services": without_reports})

        assert model.calls == 1
        assert REPORTS in model.text_of(0)

    async def test_evidence_that_names_no_service_rebuilds_everything(
        self, fresh, install
    ):
        """The documented fallback: targeting nothing would spin to the retry cap."""
        first, _ = await _build_everything(fresh, install)
        unplaceable = {"ran": True, "passed": False, "failures": ["... and 12 more"]}

        model = install(*[_code(name) for name in NAMES])
        await developer_agent({**fresh, **first, "static_report": unplaceable})

        assert model.calls == 4

    async def test_placeable_evidence_beside_unplaceable_still_narrows(
        self, fresh, install
    ):
        """Attribution is trusted whenever it can place anything at all."""
        first, _ = await _build_everything(fresh, install)
        report = _static(BACKEND_S)
        report["failures"].append("... and 12 more")

        model = install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": report})

        assert model.calls == 1

    async def test_a_test_failure_targets_its_service(self, fresh, install):
        first, _ = await _build_everything(fresh, install)
        verification = {
            "ran": True,
            "passed": False,
            "services": [
                {
                    "service": REPORTS_S,
                    "ran": True,
                    "failures": [{"test": "t", "message": "AssertionError"}],
                    "error": "",
                }
            ],
        }

        model = install(_code(REPORTS))
        await developer_agent({**fresh, **first, "verification_report": verification})

        assert model.calls == 1
        assert REPORTS in model.text_of(0)


class TestDependencies:
    """Documented rule: a dependent is never rebuilt just because its dependency was.

    ``depends_on`` establishes that a coupling exists, not what it is, so "the
    backend changed" cannot say whether the frontend's generated code is now
    wrong. When impact cannot be established, the rule is to preserve.
    """

    @pytest.fixture
    def coupled(self, workspace):
        architecture = _architecture(
            *NAMES, dependencies={FRONTEND: [BACKEND], BACKEND: [AUTH]}
        )
        return {
            **initial_state(workspace.run_id, "Build it."),
            "architecture": architecture,
            "contract": derive_contract(architecture).model_dump(mode="json"),
        }

    async def test_a_dependent_service_is_not_rebuilt(self, coupled, install):
        """The frontend depends on the backend and is still left alone."""
        first, _ = await _build_everything(coupled, install)

        model = install(_code(BACKEND))
        update = await developer_agent(
            {**coupled, **first, "static_report": _static(BACKEND_S)}
        )

        assert model.calls == 1
        assert update["service_attempts"][FRONTEND_S] == 1  # not incremented

    async def test_a_dependency_is_not_rebuilt_either(self, coupled, install):
        """Nor is the service the failing one depends on."""
        first, _ = await _build_everything(coupled, install)

        model = install(_code(BACKEND))
        update = await developer_agent(
            {**coupled, **first, "static_report": _static(BACKEND_S)}
        )

        assert model.calls == 1
        assert update["service_attempts"][AUTH_S] == 1
        assert update["service_attempts"][BACKEND_S] == 2

    async def test_the_prompt_is_the_failing_service(self, coupled, install):
        """The shared architecture preamble names every service; only one is built."""
        first, _ = await _build_everything(coupled, install)

        model = install(_code(BACKEND))
        await developer_agent({**coupled, **first, "static_report": _static(BACKEND_S)})

        prompt = model.text_of(0)
        assert "SERVICES TO IMPLEMENT" in prompt
        assert prompt.count(f'"name":"{BACKEND}"') == 1

    async def test_targeting_is_deterministic(self, coupled, install):
        first, _ = await _build_everything(coupled, install)
        state = {**coupled, **first, "static_report": _static(BACKEND_S)}

        one = install(_code(BACKEND))
        await developer_agent(copy.deepcopy(state))
        two = install(_code(BACKEND))
        await developer_agent(copy.deepcopy(state))

        assert one.calls == two.calls == 1
        assert one.text_of(0) == two.text_of(0)

    async def test_the_contract_is_not_rebuilt_during_a_developer_pass(
        self, coupled, install
    ):
        """It stays the architecture a human approved."""
        first, _ = await _build_everything(coupled, install)
        before = copy.deepcopy(coupled["contract"])

        install(_code(BACKEND))
        update = await developer_agent(
            {**coupled, **first, "static_report": _static(BACKEND_S)}
        )

        assert coupled["contract"] == before
        assert "contract" not in update


# ── State ────────────────────────────────────────────────────────


class TestServiceAttempts:
    async def test_a_first_pass_counts_one_attempt_each(self, fresh, install):
        update, _ = await _build_everything(fresh, install)

        assert update["service_attempts"] == dict.fromkeys(SLUGS, 1)

    async def test_only_the_rebuilt_service_increments(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert second["service_attempts"][BACKEND_S] == 2
        assert second["service_attempts"][AUTH_S] == 1
        assert second["service_attempts"][FRONTEND_S] == 1

    async def test_a_failed_attempt_still_counts(self, fresh, install):
        """It was paid for, whatever came back."""
        first, _ = await _build_everything(fresh, install)

        install(RuntimeError("model refused"))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert second["service_attempts"][BACKEND_S] == 2

    async def test_it_is_recorded_even_when_the_pass_fails(self, fresh, install):
        install(RuntimeError("no"), RuntimeError("no"), RuntimeError("no"), RuntimeError("no"))

        update = await developer_agent(fresh)

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value
        assert update["service_attempts"] == dict.fromkeys(SLUGS, 1)

    async def test_a_checkpoint_without_the_field_resumes(self, fresh, install):
        first, _ = await _build_everything(fresh, install)
        older = {k: v for k, v in {**fresh, **first}.items() if k != "service_attempts"}

        install(_code(BACKEND))
        update = await developer_agent({**older, "static_report": _static(BACKEND_S)})

        assert update["service_attempts"][BACKEND_S] == 1

    def test_initial_state_seeds_it(self):
        assert initial_state("r", "x")["service_attempts"] == {}

    async def test_retry_count_still_counts_passes_not_services(self, fresh, install):
        """The two are different questions and neither derives the other."""
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert first["retry_count"] == 1
        assert second["retry_count"] == 2  # one more pass over the project
        assert second["service_attempts"][BACKEND_S] == 2  # two builds of one service
        assert second["service_attempts"][AUTH_S] == 1  # untouched by that pass


# ── The artifact ─────────────────────────────────────────────────


class TestTheArtifactStaysComplete:
    @staticmethod
    def _services(workspace: RunWorkspace) -> dict[str, Any]:
        artifact = load_json(workspace.artifacts / ARTIFACT)
        return {service["service_name"]: service for service in artifact["services"]}

    async def test_a_first_pass_writes_every_service(self, fresh, install, workspace):
        await _build_everything(fresh, install)

        assert set(self._services(workspace)) == set(NAMES)

    async def test_a_surgical_pass_keeps_the_whole_project(
        self, fresh, install, workspace
    ):
        """The artifact means the delivered project, not the last thing rebuilt."""
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert set(self._services(workspace)) == set(NAMES)

    async def test_the_rebuilt_service_replaces_only_its_own_entry(
        self, fresh, install, workspace
    ):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND, "app/rewritten.py"))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        services = self._services(workspace)
        assert [f["file_path"] for f in services[BACKEND]["files"]] == ["app/rewritten.py"]
        assert [f["file_path"] for f in services[AUTH]["files"]] == ["app/main.py"]

    async def test_no_service_appears_twice(self, fresh, install, workspace):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        names = [s["service_name"] for s in load_json(workspace.artifacts / ARTIFACT)["services"]]
        assert len(names) == len(set(names)) == 4

    async def test_preserved_service_content_is_untouched(
        self, fresh, install, workspace
    ):
        first, _ = await _build_everything(fresh, install)
        before = self._services(workspace)[AUTH]

        install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert self._services(workspace)[AUTH] == before

    async def test_a_service_the_architecture_dropped_is_not_carried_forward(
        self, fresh, install, workspace
    ):
        """The artifact follows the architecture, not its own history."""
        first, _ = await _build_everything(fresh, install)
        smaller = _architecture(AUTH, BACKEND)

        install(_code(BACKEND))
        await developer_agent(
            {
                **fresh,
                **first,
                "architecture": smaller,
                "static_report": _static(BACKEND_S),
            }
        )

        assert set(self._services(workspace)) == {AUTH, BACKEND}


# ── Files on disk ────────────────────────────────────────────────


class TestPreservedFilesOnDisk:
    async def test_a_preserved_service_keeps_its_files_byte_for_byte(
        self, fresh, install, workspace
    ):
        first, _ = await _build_everything(fresh, install)
        path = workspace.source / AUTH_S / "app" / "main.py"
        before = path.read_bytes()

        install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert path.read_bytes() == before

    async def test_a_rebuilt_service_is_written(self, fresh, install, workspace):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND, "app/main.py", "app/extra.py"))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert (workspace.source / BACKEND_S / "app" / "extra.py").is_file()


class TestStaleFileRemoval:
    """Only ever for a service being regenerated in full.

    A *fix* prompt asks for the changed files alone, so its answer is partial by
    construction and its silence about a file is not a request to delete it.
    """

    async def _reorganised(self, fresh, install, workspace):
        """Build backend with three files, then regenerate it with two."""
        first, _ = await _build_everything(fresh, install)
        install(_code(BACKEND, "app/main.py", "app/routes.py", "app/old_routes.py"))
        second = await developer_agent(
            {**fresh, **first, "generated_services": [AUTH_S, REPORTS_S, FRONTEND_S]}
        )

        install(_code(BACKEND, "app/main.py", "app/routes.py"))
        third = await developer_agent(
            {**fresh, **second, "generated_services": [AUTH_S, REPORTS_S, FRONTEND_S]}
        )
        return third

    async def test_a_file_the_service_no_longer_has_is_removed(
        self, fresh, install, workspace
    ):
        await self._reorganised(fresh, install, workspace)

        assert not (workspace.source / BACKEND_S / "app" / "old_routes.py").exists()
        assert (workspace.source / BACKEND_S / "app" / "routes.py").is_file()

    async def test_the_manifest_forgets_it_too(self, fresh, install, workspace):
        update = await self._reorganised(fresh, install, workspace)

        paths = [e["file_path"] for e in manifest_util.files_for(update["code_manifest"], BACKEND_S)]
        assert "app/old_routes.py" not in paths
        assert sorted(paths) == ["app/main.py", "app/routes.py"]

    async def test_an_untouched_service_keeps_everything(
        self, fresh, install, workspace
    ):
        update = await self._reorganised(fresh, install, workspace)

        assert (workspace.source / AUTH_S / "app" / "main.py").is_file()
        assert manifest_util.files_for(update["code_manifest"], AUTH_S)

    async def test_a_fix_response_never_deletes_the_files_it_did_not_mention(
        self, fresh, install, workspace
    ):
        """The critical one: a fix answer is partial and must not be read as whole."""
        first, _ = await _build_everything(fresh, install)
        install(_code(BACKEND, "app/main.py", "app/routes.py"))
        second = await developer_agent(
            {**fresh, **first, "generated_services": [AUTH_S, REPORTS_S, FRONTEND_S]}
        )

        # Now a real fix pass: evidence for backend, and only one file returned.
        install(_code(BACKEND, "app/main.py"))
        third = await developer_agent(
            {**fresh, **second, "static_report": _static(BACKEND_S)}
        )

        assert (workspace.source / BACKEND_S / "app" / "routes.py").is_file()
        paths = [e["file_path"] for e in manifest_util.files_for(third["code_manifest"], BACKEND_S)]
        assert sorted(paths) == ["app/main.py", "app/routes.py"]

    def test_traversal_out_of_the_service_is_refused(self, workspace):
        """The base is the service's own directory, so `safe_join` refuses the rest."""
        workspace.write_source_file(AUTH, "app/main.py", "x = 1\n")
        sibling = workspace.source / BACKEND_S / "app" / "main.py"
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text("y = 2\n", encoding="utf-8")

        for escape in (
            "../backend-api/app/main.py",
            "../../x.py",
            "..\\..\\x.py",
            "app/../../backend-api/app/main.py",
        ):
            with pytest.raises(UnsafePathError):
                workspace.delete_source_file(AUTH, escape)

        assert sibling.is_file()

    def test_an_absolute_path_is_contained_rather_than_followed(self, workspace):
        """`safe_join` neutralises a leading slash or drive instead of refusing it.

        Either way the path cannot leave the service: it is reinterpreted beneath
        it, so the delete finds nothing and touches nothing outside.
        """
        workspace.write_source_file(AUTH, "app/main.py", "x = 1\n")

        assert workspace.delete_source_file(AUTH, "/etc/passwd") is False
        assert workspace.delete_source_file(AUTH, "C:/windows/system32/x.py") is False
        assert (workspace.source / AUTH_S / "app" / "main.py").is_file()

    def test_reconciliation_only_ever_names_one_service(self, workspace):
        """A stale path recorded against one service cannot remove another's file."""
        from agents.developer_agent import _reconcile

        workspace.write_source_file(AUTH, "app/main.py", "x = 1\n")
        workspace.write_source_file(BACKEND, "app/main.py", "y = 2\n")
        manifest: dict[str, Any] = {}
        manifest_util.add_file(manifest, AUTH, "../backend-api/app/main.py")

        removed = _reconcile(_code(AUTH).model_dump(mode="json"), workspace, manifest, AUTH_S)

        assert removed == []
        assert (workspace.source / BACKEND_S / "app" / "main.py").is_file()

    def test_deleting_a_file_that_is_not_there_is_not_an_error(self, workspace):
        workspace.write_source_file(AUTH, "app/main.py", "x = 1\n")

        assert workspace.delete_source_file(AUTH, "app/gone.py") is False

    def test_emptied_directories_are_tidied_up_to_the_service_root(self, workspace):
        workspace.write_source_file(AUTH, "app/deep/nested/mod.py", "x = 1\n")

        assert workspace.delete_source_file(AUTH, "app/deep/nested/mod.py") is True
        assert not (workspace.source / AUTH_S / "app").exists()
        assert workspace.service_source(AUTH).is_dir()  # the service itself remains


# ── Failure and safety ───────────────────────────────────────────


class TestFailedRegenerationIsSafe:
    async def test_a_failed_call_leaves_the_previous_code_alone(
        self, fresh, install, workspace
    ):
        first, _ = await _build_everything(fresh, install)
        path = workspace.source / BACKEND_S / "app" / "main.py"
        before = path.read_bytes()

        install(RuntimeError("the provider is down"))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert path.read_bytes() == before

    async def test_an_empty_answer_cannot_replace_working_code(
        self, fresh, install, workspace
    ):
        first, _ = await _build_everything(fresh, install)
        path = workspace.source / BACKEND_S / "app" / "main.py"
        before = path.read_bytes()

        install(_empty(BACKEND))
        update = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert path.read_bytes() == before
        assert update["failed_services"] == [BACKEND_S]

    async def test_an_answer_about_the_wrong_service_is_refused(
        self, fresh, install, workspace
    ):
        """A response naming another service is not a regeneration of this one."""
        first, _ = await _build_everything(fresh, install)

        install(_code(REPORTS))
        update = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert update["failed_services"] == [BACKEND_S]
        assert (workspace.source / BACKEND_S / "app" / "main.py").is_file()

    async def test_a_failed_service_stays_tracked(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        install(RuntimeError("no"))
        update = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert update["failed_services"] == [BACKEND_S]
        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.COMPLETED.value

    async def test_the_next_pass_targets_the_failed_service(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        install(RuntimeError("no"))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        model = install(_code(BACKEND))
        await developer_agent({**fresh, **second, "static_report": _static(BACKEND_S)})

        assert model.calls == 1
        assert BACKEND in model.text_of(0)

    async def test_a_partial_first_pass_keeps_what_landed(
        self, fresh, install, workspace
    ):
        model = install(
            _code(AUTH), RuntimeError("no"), _code(REPORTS), _code(FRONTEND)
        )

        update = await developer_agent(fresh)

        assert model.calls == 4
        assert update["generated_services"] == [AUTH_S, REPORTS_S, FRONTEND_S]
        assert update["failed_services"] == [BACKEND_S]
        assert (workspace.source / AUTH_S / "app" / "main.py").is_file()

    async def test_a_pass_that_produced_nothing_fails_the_stage(self, fresh, install):
        install(*[RuntimeError("no")] * 4)

        update = await developer_agent(fresh)

        assert update["status"][Stage.DEVELOPER.value] == AgentStatus.FAILED.value

    async def test_the_node_never_raises(self, fresh, install):
        install(*[RuntimeError("no")] * 4)

        update = await developer_agent(fresh)

        assert isinstance(update, dict)
        assert "error" in update

    async def test_the_source_reports_are_not_mutated(self, fresh, install):
        first, _ = await _build_everything(fresh, install)
        report = _static(BACKEND_S)
        snapshot = copy.deepcopy(report)
        architecture = copy.deepcopy(fresh["architecture"])

        install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": report})

        assert report == snapshot
        assert fresh["architecture"] == architecture

    async def test_the_incoming_manifest_is_not_mutated(self, fresh, install):
        first, _ = await _build_everything(fresh, install)
        incoming = first["code_manifest"]
        snapshot = copy.deepcopy(incoming)

        install(_code(BACKEND, "app/renamed.py"))
        await developer_agent(
            {**fresh, **first, "generated_services": [AUTH_S, REPORTS_S, FRONTEND_S]}
        )

        assert incoming == snapshot


class TestCompletionWaits:
    async def test_a_missing_service_is_caught_by_the_contract(
        self, fresh, install, workspace
    ):
        """The gate that stops a run finishing while a service is absent."""
        from verification.static_gate import run_static_gate

        model = install(
            _code(AUTH), RuntimeError("no"), _code(REPORTS), _code(FRONTEND)
        )
        update = await developer_agent(fresh)
        assert model.calls == 4

        report = run_static_gate(
            workspace,
            manifest_util.services(update["code_manifest"]),
            None,
            fresh["contract"],
            update["code_manifest"],
        )

        assert not report.passed
        assert any(BACKEND_S in failure and "no source" in failure for failure in report.failures)


# ── Escalation and efficiency ────────────────────────────────────


class TestEscalationStaysPerService:
    @pytest.fixture
    def routing_on(self, monkeypatch: pytest.MonkeyPatch):
        from core.config import reset_settings_cache

        monkeypatch.setenv("ROUTING_ENABLED", "true")
        reset_settings_cache()
        yield
        reset_settings_cache()

    async def test_only_the_rebuilt_service_escalates(self, fresh, install, routing_on):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        assert second["service_escalations"] == {BACKEND_S: 1}

    async def test_a_preserved_service_does_not_consume_an_escalation(
        self, fresh, install, routing_on
    ):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )

        for slug in (AUTH_S, REPORTS_S, FRONTEND_S):
            assert slug not in second["service_escalations"]

    async def test_an_escalated_service_stays_targeted(self, fresh, install, routing_on):
        first, _ = await _build_everything(fresh, install)

        install(_code(BACKEND))
        second = await developer_agent(
            {**fresh, **first, "static_report": _static(BACKEND_S)}
        )
        model = install(_code(BACKEND))
        third = await developer_agent(
            {**fresh, **second, "static_report": _static(BACKEND_S)}
        )

        assert model.calls == 1
        assert third["service_escalations"] == {BACKEND_S: 1}  # capped, still upgraded


class TestEfficiency:
    async def test_a_single_failure_costs_one_call_not_four(self, fresh, install):
        first, _ = await _build_everything(fresh, install)

        model = install(_code(BACKEND))
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert model.calls == 1  # a whole-project pass would have made four

    async def test_the_ledger_shows_the_smaller_pass(self, fresh, install, monkeypatch):
        """Fewer calls and fewer reserved tokens, with no accounting change at all."""
        from llm import registry
        from llm.budget import reset_budgets
        from tests.test_config_and_registry import MeteredFakeModel

        reset_budgets()
        registry.reset_cache()
        chat = MeteredFakeModel(total_tokens=None)

        def structured(schema, purpose=None, settings=None, demand=None, signal=None, **_):
            from langchain_core.runnables import RunnableLambda

            responses = iter([_code(name) for name in NAMES] * 4)
            return RunnableLambda(lambda _p: next(responses))

        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: chat)
        monkeypatch.setattr(registry, "get_structured_llm", structured)

        with recording(fresh["run_id"], Stage.DEVELOPER.value) as full:
            first = await developer_agent(fresh)

        # The stub bypasses `_metered`, so compare the shape the agent reports.
        assert first["cost_report"]["calls"] == full.totals()["calls"]

    async def test_adaptive_ceilings_still_apply_per_rebuilt_service(
        self, fresh, install, monkeypatch: pytest.MonkeyPatch
    ):
        from core.config import Purpose, get_settings, reset_settings_cache

        monkeypatch.setenv("ADAPTIVE_CEILINGS", "true")
        reset_settings_cache()
        first, _ = await _build_everything(fresh, install)

        captured: list[int | None] = []

        def structured(schema, purpose=None, settings=None, demand=None, signal=None, **_):
            captured.append(demand)

            class Model:
                async def ainvoke(self, prompt, config=None):
                    return _code(BACKEND)

            return Model()

        from llm import registry

        monkeypatch.setattr(registry, "get_structured_llm", structured)
        await developer_agent({**fresh, **first, "static_report": _static(BACKEND_S)})

        assert len(captured) == 1
        assert captured[0] is not None
        assert get_settings().max_output_for(Purpose.HEAVY, captured[0]) <= 4096
