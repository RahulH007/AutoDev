"""Mapping verification failures onto the services they implicate.

The retry loop currently sends the whole of the failure evidence to every service
it regenerates. The information needed to stop doing that is already in the
reports: a static check names its service, a test result names its service, and a
QA bug names a file the manifest can place. These tests establish that mapping
before anything acts on it.

Three properties matter more than any individual case, and are checked
throughout: nothing is guessed, nothing is mutated, and identical input gives
identical output.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agents.attribution import (
    SOURCE_QA,
    SOURCE_RUNNER,
    SOURCE_STATIC,
    SOURCE_TEST,
    UNATTRIBUTED,
    attribution_map,
    collect_failures,
    failures_for_service,
    implicated_services,
    reports_for_service,
)
from core import manifest as manifest_util
from prompts.developer_json_prompt import build_failure_evidence

BACKEND = "backend-api"
FRONTEND = "frontend-web"


# ── Fixtures, built the way the pipeline builds them ─────────────


def manifest() -> dict[str, Any]:
    """Two services, with one filename deliberately common to both."""
    built: dict[str, Any] = {}
    manifest_util.add_file(built, "Backend API", "app/main.py", language="python")
    manifest_util.add_file(built, "Backend API", "app/auth.py", language="python")
    manifest_util.add_file(built, "Backend API", "shared/config.py", language="python")
    manifest_util.add_file(built, "Frontend Web", "src/App.jsx", language="javascript")
    manifest_util.add_file(built, "Frontend Web", "shared/config.py", language="python")
    return built


def static_report(*, ran: bool = True) -> dict[str, Any]:
    """The shape `run_static_gate` produces: per-check plus a flattened list."""
    return {
        "ran": ran,
        "passed": False,
        "checks": [
            {
                "name": "compile",
                "service": BACKEND,
                "passed": False,
                "failures": [f"{BACKEND}/app/main.py:12: invalid syntax"],
            },
            {
                "name": "pyflakes",
                "service": BACKEND,
                "passed": False,
                "failures": [f"{BACKEND}/app/auth.py:3:1: F821 undefined name 'jwt'"],
            },
            {"name": "compile", "service": FRONTEND, "passed": True, "failures": []},
        ],
        "failures": [
            f"{BACKEND}/app/main.py:12: invalid syntax",
            f"{BACKEND}/app/auth.py:3:1: F821 undefined name 'jwt'",
        ],
    }


def verification_report() -> dict[str, Any]:
    return {
        "ran": True,
        "passed": False,
        "services": [
            {
                "service": BACKEND,
                "ran": True,
                "passed": 2,
                "failed": 1,
                "errors": 0,
                "failures": [
                    {
                        "test": "tests/test_auth.py::test_login",
                        "file": "tests/test_auth.py",
                        "message": "AssertionError: expected 200, got 500",
                    }
                ],
                "error": "",
            },
            {
                "service": FRONTEND,
                "ran": False,
                "failures": [],
                "error": "Dependency install failed: npm not found.",
            },
        ],
    }


def qa_report() -> dict[str, Any]:
    return {
        "critical_issues": 1,
        "service_reports": [
            {
                "service_name": "Backend API",
                "code_quality_score": 5,
                "bugs": [
                    {
                        "file_path": "app/auth.py",
                        "line_number": "14",
                        "severity": "critical",
                        "description": "Passwords are stored in plaintext.",
                        "suggested_fix": "Hash with bcrypt before persisting.",
                    }
                ],
            },
            {
                "service_name": "Frontend Web",
                "code_quality_score": 8,
                "bugs": [
                    {
                        "file_path": f"{FRONTEND}/src/App.jsx",
                        "line_number": "40",
                        "severity": "minor",
                        "description": "The token is read before the guard runs.",
                        "suggested_fix": "Move the guard above the read.",
                    }
                ],
            },
        ],
    }


def everything() -> dict[str, Any]:
    return {
        "static_report": static_report(),
        "verification_report": verification_report(),
        "qa_report": qa_report(),
        "manifest": manifest(),
    }


def _texts(failures: list[Any]) -> list[str]:
    return [failure.text for failure in failures]


# ── Static failures ──────────────────────────────────────────────


class TestStaticAttribution:
    def test_a_compile_failure_lands_on_its_service(self):
        found = failures_for_service(BACKEND, static_report=static_report())

        assert f"{BACKEND}/app/main.py:12: invalid syntax" in _texts(found)

    def test_the_failure_text_is_reproduced_exactly(self):
        original = static_report()
        found = failures_for_service(BACKEND, static_report=original)

        assert _texts(found) == original["checks"][0]["failures"] + original["checks"][1][
            "failures"
        ]

    def test_every_check_for_a_service_contributes(self):
        """compile and pyflakes are two checks over one service."""
        found = failures_for_service(BACKEND, static_report=static_report())

        assert len(found) == 2
        assert {failure.source for failure in found} == {SOURCE_STATIC}

    def test_a_service_that_passed_has_nothing(self):
        assert failures_for_service(FRONTEND, static_report=static_report()) == []

    def test_the_file_is_carried_alongside_the_line(self):
        found = failures_for_service(BACKEND, static_report=static_report())

        assert found[0].file == f"{BACKEND}/app/main.py"

    def test_a_report_with_only_a_flat_list_is_read_by_its_prefix(self):
        """An older report, or one whose checks were not kept."""
        report = {"ran": True, "passed": False, "failures": static_report()["failures"]}

        found = failures_for_service(BACKEND, static_report=report)

        assert len(found) == 2

    def test_the_truncation_notice_names_no_service(self):
        """`run_static_gate` appends this when there are more than forty."""
        report = {"ran": True, "passed": False, "failures": ["... and 12 more"]}

        assert implicated_services(static_report=report) == []
        assert _texts(failures_for_service(UNATTRIBUTED, static_report=report)) == [
            "... and 12 more"
        ]

    def test_the_no_services_placeholder_is_not_a_service(self):
        """`run_static_gate` writes service "-" when it found nothing to check."""
        report = {
            "ran": False,
            "checks": [{"name": "compile", "service": "-", "skipped": True, "failures": []}],
            "failures": [],
        }

        assert implicated_services(static_report=report) == []


# ── Test failures ────────────────────────────────────────────────


class TestVerificationAttribution:
    def test_a_failing_test_lands_on_its_service(self):
        found = failures_for_service(BACKEND, verification_report=verification_report())

        assert any("test_login" in text for text in _texts(found))

    def test_the_message_is_reproduced_exactly(self):
        found = failures_for_service(BACKEND, verification_report=verification_report())

        assert "AssertionError: expected 200, got 500" in found[0].text
        assert found[0].source == SOURCE_TEST

    def test_a_runner_error_is_attributed_and_kept_distinct(self):
        """A harness that never started is not the same as code that is wrong."""
        found = failures_for_service(FRONTEND, verification_report=verification_report())

        assert _texts(found) == ["Dependency install failed: npm not found."]
        assert found[0].source == SOURCE_RUNNER

    def test_the_service_field_is_taken_at_its_word(self):
        """The test runner already slugged it from the directory it ran in."""
        assert implicated_services(verification_report=verification_report()) == [
            BACKEND,
            FRONTEND,
        ]

    def test_a_service_result_with_nothing_wrong_contributes_nothing(self):
        report = {
            "ran": True,
            "passed": True,
            "services": [{"service": BACKEND, "ran": True, "passed": 4, "failures": []}],
        }

        assert implicated_services(verification_report=report) == []

    def test_a_result_naming_no_service_is_unattributed(self):
        report = {"ran": True, "services": [{"service": "", "error": "something went wrong"}]}

        assert implicated_services(verification_report=report) == []
        assert len(failures_for_service(UNATTRIBUTED, verification_report=report)) == 1


# ── QA bugs, mapped through the manifest ─────────────────────────


class TestQaAttribution:
    def test_a_bare_path_is_resolved_through_the_manifest(self):
        found = failures_for_service(BACKEND, qa_report=qa_report(), manifest=manifest())

        assert any("app/auth.py" in text for text in _texts(found))
        assert found[0].source == SOURCE_QA

    def test_a_qualified_path_is_resolved_directly(self):
        found = failures_for_service(FRONTEND, qa_report=qa_report(), manifest=manifest())

        assert any("src/App.jsx" in text for text in _texts(found))

    def test_the_description_is_reproduced_exactly(self):
        found = failures_for_service(BACKEND, qa_report=qa_report(), manifest=manifest())

        assert "Passwords are stored in plaintext." in found[0].text

    def test_the_severity_and_file_are_carried(self):
        found = failures_for_service(BACKEND, qa_report=qa_report(), manifest=manifest())

        assert found[0].severity == "critical"
        assert found[0].file == "app/auth.py"

    def test_a_windows_path_resolves_the_same_way(self):
        report = _one_bug("app\\auth.py")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND]

    def test_a_leading_slash_resolves_the_same_way(self):
        report = _one_bug("/app/auth.py")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND]

    def test_a_dot_prefixed_path_resolves_the_same_way(self):
        report = _one_bug("./app/auth.py")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND]

    def test_a_new_file_under_a_known_service_still_resolves(self):
        """The developer may have added a file the manifest has not caught up with."""
        report = _one_bug(f"{BACKEND}/app/brand_new.py")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND]

    def test_an_unresolvable_path_falls_back_to_the_reviewing_service(self):
        """The enclosing report names a service; that is data, not a guess."""
        report = _one_bug("nowhere/at/all.py", service_name="Backend API")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND]

    def test_a_bug_with_no_resolvable_service_at_all_is_unattributed(self):
        report = _one_bug("nowhere/at/all.py", service_name="A Service Nobody Built")

        assert implicated_services(qa_report=report, manifest=manifest()) == []
        assert len(failures_for_service(UNATTRIBUTED, qa_report=report, manifest=manifest())) == 1

    def test_without_a_manifest_nothing_is_invented(self):
        """No manifest means no way to place a file. Say so rather than guess."""
        assert implicated_services(qa_report=qa_report()) == []

    def test_no_service_field_is_added_to_the_qa_data(self):
        """Attribution reads what is there; it does not annotate the report."""
        report = qa_report()
        failures_for_service(BACKEND, qa_report=report, manifest=manifest())

        assert "service" not in report["service_reports"][0]["bugs"][0]


class TestAmbiguousPaths:
    """A bare path present in two services is genuinely ambiguous.

    Picking one would be a guess and dropping it would lose evidence, so the
    reviewing service settles it where it can and both are named where it cannot.
    """

    def test_the_reviewing_service_settles_a_shared_path(self):
        report = _one_bug("shared/config.py", service_name="Frontend Web")

        assert implicated_services(qa_report=report, manifest=manifest()) == [FRONTEND]

    def test_a_shared_path_with_no_usable_reviewer_implicates_both(self):
        report = _one_bug("shared/config.py", service_name="Some Other Thing")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND, FRONTEND]

    def test_both_services_receive_the_same_line(self):
        report = _one_bug("shared/config.py", service_name="Some Other Thing")
        kwargs = {"qa_report": report, "manifest": manifest()}

        assert _texts(failures_for_service(BACKEND, **kwargs)) == _texts(
            failures_for_service(FRONTEND, **kwargs)
        )

    def test_a_qualified_shared_path_is_not_ambiguous_at_all(self):
        report = _one_bug(f"{BACKEND}/shared/config.py", service_name="Some Other Thing")

        assert implicated_services(qa_report=report, manifest=manifest()) == [BACKEND]


# ── Everything together ──────────────────────────────────────────


class TestCombinedEvidence:
    def test_one_service_collects_all_three_kinds(self):
        found = failures_for_service(BACKEND, **everything())

        assert {failure.source for failure in found} == {SOURCE_STATIC, SOURCE_TEST, SOURCE_QA}

    def test_the_most_objective_evidence_comes_first(self):
        """Compiler output, then real test results, then opinion."""
        sources = [failure.source for failure in failures_for_service(BACKEND, **everything())]

        assert sources == [SOURCE_STATIC, SOURCE_STATIC, SOURCE_TEST, SOURCE_QA]

    def test_every_implicated_service_is_named(self):
        assert implicated_services(**everything()) == [BACKEND, FRONTEND]

    def test_a_service_with_no_evidence_is_not_named(self):
        """The point of the exercise: leaving healthy services alone."""
        built = manifest()
        manifest_util.add_file(built, "Worker", "worker/run.py")

        assert "worker" not in implicated_services(**{**everything(), "manifest": built})

    def test_the_map_covers_every_service_and_the_remainder(self):
        mapped = attribution_map(**everything())

        assert set(mapped) == {BACKEND, FRONTEND}
        assert mapped[BACKEND] == _texts(failures_for_service(BACKEND, **everything()))

    def test_the_map_keys_are_sorted(self):
        assert list(attribution_map(**everything())) == sorted(attribution_map(**everything()))

    def test_the_map_is_empty_when_nothing_failed(self):
        assert attribution_map() == {}
        assert implicated_services() == []
        assert failures_for_service(BACKEND) == []


class TestCollectFailures:
    """The single pass the other three functions are built on."""

    def test_it_returns_every_failure_across_every_source(self):
        collected = collect_failures(**everything())

        assert len(collected) == 6  # 2 static, 1 test, 1 runner error, 2 QA bugs

    def test_each_failure_carries_its_source_and_service(self):
        by_source: dict[str, set[str]] = {}
        for failure in collect_failures(**everything()):
            by_source.setdefault(failure.source, set()).add(failure.service)

        assert by_source[SOURCE_STATIC] == {BACKEND}
        assert by_source[SOURCE_TEST] == {BACKEND}
        assert by_source[SOURCE_RUNNER] == {FRONTEND}
        assert by_source[SOURCE_QA] == {BACKEND, FRONTEND}

    def test_a_failure_is_frozen(self):
        """Records are handed out; a caller must not be able to re-attribute one."""
        import dataclasses

        failure = collect_failures(**everything())[0]

        with pytest.raises(dataclasses.FrozenInstanceError):
            failure.service = "somewhere-else"  # type: ignore[misc]

    def test_no_arguments_is_no_failures(self):
        assert collect_failures() == []


class TestNoDuplicates:
    def test_the_same_line_from_two_checks_is_kept_once(self):
        line = f"{BACKEND}/app/main.py:12: invalid syntax"
        report = {
            "ran": True,
            "checks": [
                {"name": "compile", "service": BACKEND, "failures": [line]},
                {"name": "pyflakes", "service": BACKEND, "failures": [line]},
            ],
        }

        assert _texts(failures_for_service(BACKEND, static_report=report)) == [line]

    def test_the_map_carries_no_repeats_either(self):
        line = f"{BACKEND}/app/main.py:12: invalid syntax"
        report = {
            "ran": True,
            "checks": [
                {"name": "compile", "service": BACKEND, "failures": [line, line]},
            ],
        }

        assert attribution_map(static_report=report) == {BACKEND: [line]}

    def test_identical_text_from_different_sources_is_not_a_duplicate(self):
        """A compiler and a reviewer saying the same words are two facts."""
        shared = "app/auth.py is wrong"
        static = {"ran": True, "checks": [{"service": BACKEND, "failures": [shared]}]}
        found = failures_for_service(BACKEND, static_report=static, qa_report=qa_report(),
                                     manifest=manifest())

        assert len(found) == 2


class TestCanonicalSlugs:
    def test_a_display_name_finds_the_same_failures_as_its_slug(self):
        by_name = failures_for_service("Backend API", **everything())
        by_slug = failures_for_service(BACKEND, **everything())

        assert _texts(by_name) == _texts(by_slug)

    def test_spelling_variations_agree(self):
        expected = _texts(failures_for_service(BACKEND, **everything()))

        for spelling in ("backend api", "Backend  API", "BACKEND-API", "backend_api"):
            assert _texts(failures_for_service(spelling, **everything())) == expected

    def test_the_map_is_keyed_by_slug_not_display_name(self):
        assert "Backend API" not in attribution_map(**everything())

    def test_an_empty_name_matches_nothing(self):
        assert failures_for_service("", **everything()) == []
        assert failures_for_service("   ", **everything()) == []

    def test_the_unattributed_key_can_never_collide_with_a_slug(self):
        """`slugify` strips underscores, so no service name can produce this key."""
        from core.paths import slugify

        assert slugify(UNATTRIBUTED) != UNATTRIBUTED
        assert not UNATTRIBUTED[0].isalnum()


class TestDeterminism:
    def test_the_same_input_gives_the_same_answer(self):
        assert attribution_map(**everything()) == attribution_map(**everything())

    def test_repeated_calls_agree_on_order(self):
        first = _texts(failures_for_service(BACKEND, **everything()))
        second = _texts(failures_for_service(BACKEND, **everything()))

        assert first == second

    def test_the_answer_does_not_depend_on_manifest_insertion_order(self):
        forwards: dict[str, Any] = {}
        manifest_util.add_file(forwards, "Backend API", "app/auth.py")
        manifest_util.add_file(forwards, "Frontend Web", "src/App.jsx")

        backwards: dict[str, Any] = {}
        manifest_util.add_file(backwards, "Frontend Web", "src/App.jsx")
        manifest_util.add_file(backwards, "Backend API", "app/auth.py")

        assert attribution_map(qa_report=qa_report(), manifest=forwards) == attribution_map(
            qa_report=qa_report(), manifest=backwards
        )

    def test_implicated_services_is_sorted(self):
        report = verification_report()
        report["services"].reverse()

        assert implicated_services(verification_report=report) == [BACKEND, FRONTEND]


class TestNothingIsMutated:
    """The reports belong to the run, and other stages are still reading them."""

    def test_the_static_report_is_untouched(self):
        original = static_report()
        snapshot = copy.deepcopy(original)

        attribution_map(static_report=original, manifest=manifest())

        assert original == snapshot

    def test_the_verification_report_is_untouched(self):
        original = verification_report()
        snapshot = copy.deepcopy(original)

        attribution_map(verification_report=original, manifest=manifest())

        assert original == snapshot

    def test_the_qa_report_is_untouched(self):
        original = qa_report()
        snapshot = copy.deepcopy(original)

        attribution_map(qa_report=original, manifest=manifest())

        assert original == snapshot

    def test_the_manifest_is_untouched(self):
        original = manifest()
        snapshot = copy.deepcopy(original)

        attribution_map(**{**everything(), "manifest": original})

        assert original == snapshot

    def test_narrowing_does_not_reach_back_into_the_originals(self):
        reports = everything()
        snapshot = copy.deepcopy(reports)

        narrowed = reports_for_service(BACKEND, **reports)
        for report in narrowed:
            report["injected"] = True
        for section in narrowed[1].get("services", []):
            section["service"] = "tampered"

        assert reports == snapshot


# ── The Phase 2 seam ─────────────────────────────────────────────


class TestReportsForService:
    """Narrowed reports, shaped to feed the existing evidence renderer.

    Nothing calls this yet — Phase 2 does. It is tested now so the mapping and
    the use it was designed for are reviewed together.
    """

    def test_the_narrowed_reports_render_through_the_existing_builder(self):
        static, verification, qa = reports_for_service(BACKEND, **everything())

        evidence = build_failure_evidence(qa, static, verification)

        assert "invalid syntax" in evidence
        assert "test_login" in evidence
        assert "plaintext" in evidence

    def test_another_service_evidence_is_absent(self):
        static, verification, qa = reports_for_service(BACKEND, **everything())

        evidence = build_failure_evidence(qa, static, verification)

        assert "App.jsx" not in evidence
        assert "npm not found" not in evidence

    def test_a_service_with_nothing_wrong_renders_no_evidence(self):
        built = manifest()
        manifest_util.add_file(built, "Worker", "worker/run.py")

        static, verification, qa = reports_for_service(
            "Worker", **{**everything(), "manifest": built}
        )

        assert build_failure_evidence(qa, static, verification) == ""

    def test_an_empty_section_is_omitted_rather_than_left_hollow(self):
        static, verification, qa = reports_for_service(
            FRONTEND, static_report=static_report(), manifest=manifest()
        )

        assert static == {}
        assert verification == {}
        assert qa == {}

    def test_the_runner_error_survives_narrowing(self):
        _, verification, _ = reports_for_service(FRONTEND, **everything())

        assert verification["services"][0]["error"] == "Dependency install failed: npm not found."

    def test_an_unknown_service_narrows_to_nothing(self):
        assert reports_for_service("nobody", **everything()) == ({}, {}, {})


# ── Helpers ──────────────────────────────────────────────────────


def _one_bug(file_path: str, service_name: str = "Backend API") -> dict[str, Any]:
    return {
        "service_reports": [
            {
                "service_name": service_name,
                "code_quality_score": 6,
                "bugs": [
                    {
                        "file_path": file_path,
                        "line_number": "1",
                        "severity": "major",
                        "description": "Something is wrong here.",
                        "suggested_fix": "Fix it.",
                    }
                ],
            }
        ]
    }
