"""Each service is shown only the failures that are actually its own.

Until now a fix pass handed every service the whole project's evidence: a
frontend being rebuilt was shown the backend's compiler errors, and a service
with nothing wrong with it was shown everything. `agents/attribution.py` already
knew which service each failure implicated; this is where that finally changes
what a model is asked to read.

Two things are being checked at once, and the second matters as much as the
first. That the narrowing happens — and that nothing else did. Same services, in
the same order, the same number of model calls, the same instructions, the same
failure text, and no evidence quietly lost on the way.

Everything runs against a recording fake standing in for the registry, so the
prompt each service actually received can be read back.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agents.developer_agent import developer_agent
from core import manifest as manifest_util
from llm.budget import estimate_tokens
from prompts.developer_json_prompt import build_failure_evidence, get_developer_prompt
from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode
from state.state import AgentStatus, Stage
from tests import fakes

BACKEND, FRONTEND = "Backend API", "Frontend Web"
BACKEND_SLUG, FRONTEND_SLUG = "backend-api", "frontend-web"

# Text unique to one service's failures, so a prompt can be asked which it holds.
BACKEND_SYNTAX = f"{BACKEND_SLUG}/app/main.py:12: invalid syntax"
FRONTEND_SYNTAX = f"{FRONTEND_SLUG}/src/App.jsx:4: unexpected token"
BACKEND_TEST = "AssertionError: expected 200, got 500"
FRONTEND_TEST = "AssertionError: the button never rendered"
BACKEND_BUG = "Passwords are stored in plaintext."
FRONTEND_BUG = "The token is read before the guard runs."
GLOBAL_FAILURE = "... and 12 more"


# ── Fixtures ─────────────────────────────────────────────────────


def _service(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} service.",
        "tech_stack": ["Python"],
        "dependencies": [],
        "api_endpoints": [],
        "data_models": [],
    }


def _architecture(*names: str) -> dict[str, Any]:
    return {
        "system_overview": "Two services.",
        "architecture_style": "modular_monolith",
        "services": [_service(name) for name in names],
        "databases": [],
        "environment_variables": [],
    }


def _code_for(service_name: str) -> DeveloperSchema:
    return DeveloperSchema(
        project_name="SpendWise",
        services=[
            ServiceCode(
                service_name=service_name,
                files=[
                    CodeFile(
                        file_path="app/main.py",
                        file_name="main.py",
                        language="python",
                        code=fakes.CALCULATOR_SOURCE,
                        description=f"Entry point for {service_name}.",
                    )
                ],
            )
        ],
    )


def _manifest() -> dict[str, Any]:
    built: dict[str, Any] = {}
    manifest_util.add_file(built, BACKEND, "app/main.py", language="python")
    manifest_util.add_file(built, BACKEND, "app/auth.py", language="python")
    manifest_util.add_file(built, FRONTEND, "src/App.jsx", language="javascript")
    return built


def _static(*, backend: bool = True, frontend: bool = True, unattributed: bool = False):
    checks = []
    failures = []
    if backend:
        checks.append({"name": "compile", "service": BACKEND_SLUG, "failures": [BACKEND_SYNTAX]})
        failures.append(BACKEND_SYNTAX)
    if frontend:
        checks.append({"name": "compile", "service": FRONTEND_SLUG, "failures": [FRONTEND_SYNTAX]})
        failures.append(FRONTEND_SYNTAX)
    if unattributed:
        # `run_static_gate` appends this when a run breaks in more than forty
        # places. It names no service and never could.
        checks.append({"name": "compile", "service": "-", "failures": [GLOBAL_FAILURE]})
        failures.append(GLOBAL_FAILURE)
    return {"ran": True, "passed": False, "checks": checks, "failures": failures}


def _verification(*, backend: bool = True, frontend: bool = True):
    services = []
    if backend:
        services.append(
            {
                "service": BACKEND_SLUG,
                "ran": True,
                "passed": 1,
                "failed": 1,
                "failures": [{"test": "test_login", "file": "t.py", "message": BACKEND_TEST}],
                "error": "",
            }
        )
    if frontend:
        services.append(
            {
                "service": FRONTEND_SLUG,
                "ran": True,
                "passed": 0,
                "failed": 1,
                "failures": [{"test": "test_render", "file": "t.jsx", "message": FRONTEND_TEST}],
                "error": "",
            }
        )
    return {"ran": True, "passed": False, "services": services}


def _qa(*, backend: bool = True, frontend: bool = True):
    reports = []
    if backend:
        reports.append(
            {
                "service_name": BACKEND,
                "code_quality_score": 5,
                "bugs": [
                    {
                        "file_path": "app/auth.py",
                        "line_number": "14",
                        "severity": "critical",
                        "description": BACKEND_BUG,
                        "suggested_fix": "Hash with bcrypt.",
                    }
                ],
            }
        )
    if frontend:
        reports.append(
            {
                "service_name": FRONTEND,
                "code_quality_score": 6,
                "bugs": [
                    {
                        "file_path": "src/App.jsx",
                        "line_number": "40",
                        "severity": "minor",
                        "description": FRONTEND_BUG,
                        "suggested_fix": "Move the guard up.",
                    }
                ],
            }
        )
    return {"critical_issues": 1, "service_reports": reports}


class RecordingModel:
    """One canned response per call, remembering what it was asked."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any, config: Any = None) -> Any:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else _code_for(BACKEND)

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def text_of(self, index: int) -> str:
        from llm.registry import _prompt_text

        return _prompt_text(self.prompts[index])


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):
    def _install(model: RecordingModel) -> RecordingModel:
        from llm import registry

        monkeypatch.setattr(registry, "get_structured_llm", lambda *a, **k: model)
        return model

    return _install


@pytest.fixture
def state(workspace):
    """A run that has already generated both services and had them verified."""
    return {
        "run_id": "test-run",
        "user_requirements": "Build an expense tracker.",
        "prd": fakes.build_prd().model_dump(mode="json"),
        "architecture": _architecture(BACKEND, FRONTEND),
        "code_manifest": _manifest(),
        "status": {stage.value: AgentStatus.PENDING.value for stage in Stage},
        "retry_count": 1,
        "generated_services": [BACKEND_SLUG, FRONTEND_SLUG],
    }


@pytest.fixture
def two_models(install):
    return install(RecordingModel(_code_for(BACKEND), _code_for(FRONTEND)))


# ── Narrowing, one source at a time ──────────────────────────────


class TestStaticFailuresAreNarrowed:
    async def test_a_service_receives_its_own_compiler_errors(self, state, two_models):
        await developer_agent({**state, "static_report": _static()})

        assert BACKEND_SYNTAX in two_models.text_of(0)

    async def test_a_service_does_not_receive_another_services_errors(
        self, state, two_models
    ):
        """The whole point of the phase."""
        await developer_agent({**state, "static_report": _static()})

        assert FRONTEND_SYNTAX not in two_models.text_of(0)
        assert BACKEND_SYNTAX not in two_models.text_of(1)

    async def test_the_second_service_receives_its_own(self, state, two_models):
        await developer_agent({**state, "static_report": _static()})

        assert FRONTEND_SYNTAX in two_models.text_of(1)

    async def test_the_failure_text_is_unchanged(self, state, two_models):
        report = _static()
        await developer_agent({**state, "static_report": report})

        assert report["checks"][0]["failures"][0] in two_models.text_of(0)


class TestTestFailuresAreNarrowed:
    async def test_a_service_receives_its_own_assertion(self, state, two_models):
        await developer_agent({**state, "verification_report": _verification()})

        assert BACKEND_TEST in two_models.text_of(0)

    async def test_a_service_does_not_receive_another_services_assertion(
        self, state, two_models
    ):
        await developer_agent({**state, "verification_report": _verification()})

        assert FRONTEND_TEST not in two_models.text_of(0)
        assert BACKEND_TEST not in two_models.text_of(1)

    async def test_a_runner_error_follows_its_service(self, state, two_models):
        """Only the implicated service is rebuilt, and it is the one that sees it."""
        report = _verification(backend=False)
        report["services"][0]["error"] = "Dependency install failed."

        await developer_agent({**state, "verification_report": report})

        assert two_models.calls == 1  # the backend is preserved
        assert "Dependency install failed." in two_models.text_of(0)


class TestQaBugsAreNarrowed:
    async def test_a_bug_reaches_the_service_that_owns_the_file(self, state, two_models):
        """`app/auth.py` is resolved through the manifest, not the report header."""
        await developer_agent({**state, "qa_report": _qa()})

        assert BACKEND_BUG in two_models.text_of(0)

    async def test_a_bug_does_not_reach_another_service(self, state, two_models):
        await developer_agent({**state, "qa_report": _qa()})

        assert FRONTEND_BUG not in two_models.text_of(0)
        assert BACKEND_BUG not in two_models.text_of(1)

    async def test_the_suggested_fix_travels_with_the_bug(self, state, two_models):
        await developer_agent({**state, "qa_report": _qa()})

        assert "Hash with bcrypt." in two_models.text_of(0)


# ── Everything together ──────────────────────────────────────────


def _all_reports() -> dict[str, Any]:
    return {
        "static_report": _static(),
        "verification_report": _verification(),
        "qa_report": _qa(),
    }


class TestMixedEvidence:
    async def test_a_service_receives_all_three_of_its_own(self, state, two_models):
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert BACKEND_SYNTAX in prompt
        assert BACKEND_TEST in prompt
        assert BACKEND_BUG in prompt

    async def test_and_none_of_the_others(self, state, two_models):
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert FRONTEND_SYNTAX not in prompt
        assert FRONTEND_TEST not in prompt
        assert FRONTEND_BUG not in prompt

    async def test_the_ordering_is_static_then_tests_then_review(self, state, two_models):
        """Most objective first, exactly as the whole-project evidence ordered it."""
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert prompt.index(BACKEND_SYNTAX) < prompt.index(BACKEND_TEST) < prompt.index(BACKEND_BUG)

    async def test_the_section_headings_are_unchanged(self, state, two_models):
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert "COMPILE AND LINT FAILURES" in prompt
        assert "REAL TEST RESULTS" in prompt
        assert "REVIEWER-REPORTED BUGS" in prompt


class TestHealthyServices:
    async def test_a_healthy_service_is_not_rebuilt_at_all(self, state, two_models):
        """Only the backend failed, so only the backend is sent to a model.

        Narrowing the evidence was the first half of this; not paying for the
        service at all is the second. A healthy service is left exactly as it is.
        """
        update = await developer_agent({**state, "static_report": _static(frontend=False)})

        assert two_models.calls == 1
        assert update["service_attempts"] == {BACKEND_SLUG: 1}

    async def test_the_rebuilt_service_is_the_failing_one(self, state, two_models):
        await developer_agent({**state, "static_report": _static(frontend=False)})
        prompt = two_models.text_of(0)

        assert BACKEND_SYNTAX in prompt
        assert "FIX MODE" in prompt

    async def test_the_preserved_service_never_sees_the_other_failure(
        self, state, two_models
    ):
        """It is not prompted at all, which is the strongest form of not seeing it."""
        await developer_agent({**state, "static_report": _static(frontend=False)})

        assert FRONTEND not in two_models.text_of(0)
        assert len(two_models.prompts) == 1


class TestUnattributedEvidence:
    """A failure that names no service still has to reach someone.

    Nothing knows whose it is, so it goes to everyone — which is exactly what
    happens today. Narrowing without this would be the one way the change could
    lose evidence rather than merely stop repeating it.
    """

    async def test_it_reaches_every_service(self, state, two_models):
        await developer_agent({**state, "static_report": _static(unattributed=True)})

        assert GLOBAL_FAILURE in two_models.text_of(0)
        assert GLOBAL_FAILURE in two_models.text_of(1)

    async def test_it_reaches_a_service_that_has_no_failures_of_its_own(
        self, state, two_models
    ):
        report = _static(backend=False, frontend=False, unattributed=True)

        await developer_agent({**state, "static_report": report})

        assert GLOBAL_FAILURE in two_models.text_of(0)
        assert GLOBAL_FAILURE in two_models.text_of(1)

    async def test_it_is_not_assigned_to_an_arbitrary_service(self, state, two_models):
        """Shown to all is not the same as owned by one."""
        from agents.attribution import UNATTRIBUTED, attribution_map

        mapped = attribution_map(static_report=_static(unattributed=True), manifest=_manifest())

        assert mapped[UNATTRIBUTED] == [GLOBAL_FAILURE]
        assert GLOBAL_FAILURE not in mapped[BACKEND_SLUG]

    async def test_nothing_is_lost_between_the_project_and_its_services(
        self, state, two_models
    ):
        """Every line of the project-wide evidence lands somewhere."""
        reports = _all_reports()
        reports["static_report"] = _static(unattributed=True)
        await developer_agent({**state, **reports})

        seen = two_models.text_of(0) + two_models.text_of(1)
        for line in (
            BACKEND_SYNTAX, FRONTEND_SYNTAX, GLOBAL_FAILURE,
            BACKEND_TEST, FRONTEND_TEST, BACKEND_BUG, FRONTEND_BUG,
        ):
            assert line in seen, line


# ── What must not have changed ───────────────────────────────────


class TestFirstPassIsUnchanged:
    async def test_a_first_pass_carries_no_failure_evidence(self, state, two_models):
        await developer_agent({**state, "generated_services": []})
        prompt = two_models.text_of(0)

        assert "FIX MODE" not in prompt
        assert "VERIFICATION FAILED" not in prompt

    async def test_a_first_pass_still_asks_for_the_complete_output(
        self, state, two_models
    ):
        await developer_agent({**state, "generated_services": []})

        assert "Generate the complete Developer Schema output" in two_models.text_of(0)

    def test_narrowed_and_project_wide_prompts_agree_when_nothing_failed(self):
        """With no failures the two paths must produce the identical prompt."""
        from llm.registry import _prompt_text

        architecture = _architecture(BACKEND)
        prd = fakes.build_prd().model_dump(mode="json")
        service = architecture["services"][0]

        before = get_developer_prompt(
            "Build it.", prd, architecture, service=service,
            qa_report=None, static_report=None, verification_report=None,
        )
        after = get_developer_prompt(
            "Build it.", prd, architecture, service=service,
            qa_report={}, static_report={}, verification_report={},
        )

        assert _prompt_text(before) == _prompt_text(after)

    async def test_the_prd_and_architecture_projections_are_untouched(
        self, state, two_models
    ):
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert "SpendWise" in prompt
        assert "SERVICES TO IMPLEMENT" in prompt
        assert "ARCHITECTURE" in prompt


class TestInstructionsSurvive:
    """The compaction guard in test_developer_instructions covers the text itself.

    What is checked here is that a narrowed prompt still carries it.
    """

    async def test_the_base_instructions_are_present(self, state, two_models):
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert "senior full-stack developer" in prompt
        assert "no TODOs" in prompt
        assert "relative to the service root" in prompt

    async def test_fix_instructions_appear_for_an_implicated_service(
        self, state, two_models
    ):
        await developer_agent({**state, **_all_reports()})
        prompt = two_models.text_of(0)

        assert "FIX MODE" in prompt
        assert "ONLY the files you are changing" in prompt


class TestNothingElseMoved:
    async def test_every_service_is_still_generated(self, state, two_models):
        await developer_agent({**state, **_all_reports()})

        assert two_models.calls == 2

    async def test_the_model_call_count_is_one_per_service(self, state, two_models):
        update = await developer_agent({**state, **_all_reports()})

        assert two_models.calls == len(_architecture(BACKEND, FRONTEND)["services"])
        assert update["generated_services"] == [BACKEND_SLUG, FRONTEND_SLUG]

    async def test_the_service_order_is_unchanged(self, state, two_models):
        await developer_agent({**state, **_all_reports()})

        assert BACKEND in two_models.text_of(0)
        assert FRONTEND in two_models.text_of(1)

    async def test_the_retry_count_is_unchanged(self, state, two_models):
        update = await developer_agent({**state, **_all_reports()})

        assert update["retry_count"] == 2

    async def test_the_reports_are_not_mutated(self, state, two_models):
        reports = _all_reports()
        snapshot = copy.deepcopy(reports)

        await developer_agent({**state, **reports})

        assert reports == snapshot

    async def test_the_incoming_manifest_still_drives_attribution(
        self, state, two_models
    ):
        """The lens is snapshotted, so what service one writes cannot move service two.

        Without the snapshot, `_write_code` growing the manifest mid-loop would
        make the answer for a later service depend on an earlier one.
        """
        await developer_agent({**state, "qa_report": _qa()})

        assert BACKEND_BUG in two_models.text_of(0)
        assert BACKEND_BUG not in two_models.text_of(1)

    async def test_a_successful_pass_still_clears_the_evidence_and_attribution(
        self, state, two_models
    ):
        update = await developer_agent({**state, **_all_reports()})

        assert update["static_report"] == {}
        assert update["verification_report"] == {}
        assert update["service_failures"] == {}


# ── The reduction, measured ──────────────────────────────────────


class TestMeasurableReduction:
    """The point of the phase, in the units the ledger already speaks.

    `estimate_tokens` is the same pure function the budget sizes a prompt with,
    so these figures are comparable to the `estimated_tokens` the ledger records
    per call. Nothing is accumulated here — no second counter.
    """

    def test_a_services_evidence_is_smaller_than_the_projects(self):
        from agents.attribution import reports_for_service

        reports = _all_reports()
        whole = build_failure_evidence(
            reports["qa_report"], reports["static_report"], reports["verification_report"]
        )
        static_for, verification_for, qa_for = reports_for_service(
            BACKEND_SLUG, **reports, manifest=_manifest(), include_unattributed=True
        )
        part = build_failure_evidence(qa_for, static_for, verification_for)

        assert estimate_tokens(part) < estimate_tokens(whole)

    def test_a_healthy_service_pays_nothing_for_evidence(self):
        from agents.attribution import reports_for_service

        reports = {**_all_reports(), "static_report": _static(frontend=False)}
        reports["verification_report"] = _verification(frontend=False)
        reports["qa_report"] = _qa(frontend=False)

        static_for, verification_for, qa_for = reports_for_service(
            FRONTEND_SLUG, **reports, manifest=_manifest(), include_unattributed=True
        )

        assert build_failure_evidence(qa_for, static_for, verification_for) == ""

    async def test_the_saving_is_reported_per_service(self, state, two_models, caplog):
        """A run's own log says how much each service was spared."""
        import logging

        with caplog.at_level(logging.INFO, logger="agents.developer_agent"):
            await developer_agent({**state, **_all_reports()})

        lines = [r.getMessage() for r in caplog.records if "Evidence for" in r.getMessage()]
        assert len(lines) == 2
        assert all("project-wide" in line for line in lines)

    async def test_nothing_is_logged_when_nothing_failed(self, state, two_models, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="agents.developer_agent"):
            await developer_agent({**state, "generated_services": []})

        assert not [r for r in caplog.records if "Evidence for" in r.getMessage()]
