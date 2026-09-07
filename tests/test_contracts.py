"""The architecture, restated as something a filesystem can answer.

An approved architecture used to be a description read by a language model and
checked by nobody: whether the delivered code matched the design a human signed
off on was never established. A contract is that document reduced to the claims a
path lookup can settle, derived from the JSON without a model call.

Two properties are load-bearing and are checked throughout. Derivation invents
nothing the architect did not write down, and validation reports nothing it
cannot establish — a gate that cries wolf costs a developer pass and the tokens
to pay for it, so silence is the correct answer for anything ambiguous.
"""

from __future__ import annotations

import copy
from typing import Any

from agents.attribution import attribution_map, implicated_services
from core.contracts import (
    CHECK_NAME,
    ArchitectureContract,
    check_contract,
    derive_contract,
)
from core.paths import RunWorkspace
from schema.verification_schema import StaticReport
from tests import fakes
from verification.static_gate import run_static_gate

BACKEND, FRONTEND = "Backend API", "Frontend Web"
BACKEND_SLUG, FRONTEND_SLUG = "backend-api", "frontend-web"


# ── Fixtures ─────────────────────────────────────────────────────


def architecture(**overrides: Any) -> dict[str, Any]:
    """A representative two-service architecture, in the JSON shape state holds."""
    document: dict[str, Any] = {
        "system_overview": "A backend and a frontend.",
        "architecture_style": "modular_monolith",
        "services": [
            {
                "name": BACKEND,
                "description": "Serves the REST API.",
                "tech_stack": ["Python", "FastAPI"],
                "dependencies": [],
                "api_endpoints": [
                    {"method": "POST", "path": "/api/v1/expenses", "description": "Create."}
                ],
                "data_models": [
                    {"name": "Expense", "description": "A record.", "fields": ["id: int"]}
                ],
            },
            {
                "name": FRONTEND,
                "description": "The browser client.",
                "tech_stack": ["React"],
                "dependencies": [BACKEND],
                "api_endpoints": [],
                "data_models": [],
            },
        ],
        "project_structure": [
            {
                "service_name": BACKEND,
                "folders": ["app"],
                "key_files": ["app/main.py", "app/models.py"],
            },
            {"service_name": FRONTEND, "folders": ["src"], "key_files": ["src/index.jsx"]},
        ],
    }
    document.update(overrides)
    return document


def build(workspace: RunWorkspace, service: str, *paths: str) -> None:
    """Write the named files into a service, through the workspace as always."""
    for path in paths:
        workspace.write_source_file(service, path, "x = 1\n")


def complete(workspace: RunWorkspace) -> None:
    """Everything the contract above requires."""
    build(workspace, BACKEND, "app/main.py", "app/models.py")
    build(workspace, FRONTEND, "src/index.jsx")


def failures_of(checks: list[Any]) -> list[str]:
    return [failure for check in checks for failure in check.failures]


# ── Derivation ───────────────────────────────────────────────────


class TestDerivation:
    def test_every_service_becomes_a_contract(self):
        contract = derive_contract(architecture())

        assert contract.slugs == [BACKEND_SLUG, FRONTEND_SLUG]

    def test_the_architecture_service_order_is_preserved(self):
        reversed_doc = architecture()
        reversed_doc["services"].reverse()

        assert derive_contract(reversed_doc).slugs == [FRONTEND_SLUG, BACKEND_SLUG]

    def test_the_display_name_is_kept_beside_the_slug(self):
        service = derive_contract(architecture()).service(BACKEND_SLUG)

        assert service is not None
        assert service.name == BACKEND

    def test_the_project_structure_finds_its_service_by_slug(self):
        """The architect spells the service name twice and not always alike."""
        document = architecture()
        document["project_structure"][0]["service_name"] = "backend api"

        service = derive_contract(document).service(BACKEND_SLUG)

        assert service is not None
        assert service.key_files == ["app/main.py", "app/models.py"]

    def test_folders_are_carried(self):
        assert derive_contract(architecture()).service(BACKEND_SLUG).folders == ["app"]

    def test_the_entrypoint_is_singled_out_of_the_key_files(self):
        service = derive_contract(architecture()).service(BACKEND_SLUG)

        assert service.entrypoint == "app/main.py"

    def test_a_service_with_no_conventional_entrypoint_names_none(self):
        document = architecture()
        document["project_structure"][0]["key_files"] = ["app/models.py"]

        assert derive_contract(document).service(BACKEND_SLUG).entrypoint is None

    def test_dependencies_become_slugs(self):
        assert derive_contract(architecture()).service(FRONTEND_SLUG).depends_on == [BACKEND_SLUG]

    def test_endpoints_and_models_are_recorded_for_later(self):
        service = derive_contract(architecture()).service(BACKEND_SLUG)

        assert service.endpoints[0].method == "POST"
        assert service.endpoints[0].path == "/api/v1/expenses"
        assert service.data_models == ["Expense"]

    def test_the_architecture_style_is_carried(self):
        assert derive_contract(architecture()).architecture_style == "modular_monolith"

    def test_it_serialises_to_plain_data_for_state(self):
        dumped = derive_contract(architecture()).model_dump(mode="json")

        assert dumped["services"][0]["slug"] == BACKEND_SLUG
        assert ArchitectureContract.model_validate(dumped).slugs == [BACKEND_SLUG, FRONTEND_SLUG]

    def test_a_path_that_tries_to_escape_is_not_recorded(self):
        """The architecture is model-written; a contract is not a way round safe_join."""
        document = architecture()
        document["project_structure"][0]["key_files"] = ["../../etc/passwd", "app/main.py"]

        assert derive_contract(document).service(BACKEND_SLUG).key_files == ["app/main.py"]

    def test_duplicate_paths_are_kept_once(self):
        document = architecture()
        document["project_structure"][0]["key_files"] = ["app/main.py", "app/main.py"]

        assert derive_contract(document).service(BACKEND_SLUG).key_files == ["app/main.py"]

    def test_the_real_fake_architecture_derives_cleanly(self):
        """The fixture the rest of the suite drives the pipeline with."""
        contract = derive_contract(fakes.build_architecture().model_dump(mode="json"))

        service = contract.service(fakes.SERVICE_SLUG)
        assert service is not None
        assert service.key_files == ["app/calculator.py", "app/store.py"]


class TestDeterminism:
    def test_identical_input_gives_identical_output(self):
        assert derive_contract(architecture()) == derive_contract(architecture())

    def test_the_serialised_form_is_identical_too(self):
        first = derive_contract(architecture()).model_dump(mode="json")
        second = derive_contract(architecture()).model_dump(mode="json")

        assert first == second

    def test_validation_is_repeatable(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py")
        contract = derive_contract(architecture())

        first = failures_of(check_contract(contract, {}, workspace))
        second = failures_of(check_contract(contract, {}, workspace))

        assert first == second


class TestTheSourceIsNotMutated:
    def test_deriving_leaves_the_architecture_alone(self):
        document = architecture()
        snapshot = copy.deepcopy(document)

        derive_contract(document)

        assert document == snapshot

    def test_checking_leaves_the_contract_alone(self, workspace: RunWorkspace):
        contract = derive_contract(architecture()).model_dump(mode="json")
        snapshot = copy.deepcopy(contract)

        check_contract(contract, {}, workspace)

        assert contract == snapshot


class TestMissingFieldsAreSafe:
    """An architecture that omits something yields a contract with less to check.

    That is the correct answer rather than a reason to fail: the architect not
    naming a file is not the developer failing to write one.
    """

    def test_no_project_structure_at_all(self):
        document = architecture()
        del document["project_structure"]

        service = derive_contract(document).service(BACKEND_SLUG)

        assert service.key_files == []
        assert service.folders == []

    def test_no_services_at_all(self):
        assert derive_contract({"architecture_style": "monolith"}).services == []

    def test_an_empty_document(self):
        assert derive_contract({}).services == []

    def test_no_document(self):
        assert derive_contract(None).services == []

    def test_a_service_with_no_name_is_skipped(self):
        """Nothing unnamed can own a directory, so there is nothing to check."""
        document = architecture()
        document["services"].append({"description": "A nameless thing."})

        assert derive_contract(document).slugs == [BACKEND_SLUG, FRONTEND_SLUG]

    def test_a_malformed_project_structure_entry_is_ignored(self):
        document = architecture()
        document["project_structure"].append("not a mapping")

        assert derive_contract(document).slugs == [BACKEND_SLUG, FRONTEND_SLUG]

    def test_a_service_missing_optional_lists(self):
        document = {"services": [{"name": BACKEND}]}

        service = derive_contract(document).service(BACKEND_SLUG)

        assert service.endpoints == []
        assert service.data_models == []
        assert service.depends_on == []


# ── Validation ───────────────────────────────────────────────────


class TestAValidProject:
    def test_a_complete_project_has_no_violations(self, workspace: RunWorkspace):
        complete(workspace)

        checks = check_contract(derive_contract(architecture()), {}, workspace)

        assert failures_of(checks) == []
        assert all(check.passed for check in checks)

    def test_extra_files_beyond_the_contract_are_fine(self, workspace: RunWorkspace):
        """The contract is a floor, not a ceiling."""
        complete(workspace)
        build(workspace, BACKEND, "app/extra.py", "app/routers/auth.py")

        assert failures_of(check_contract(derive_contract(architecture()), {}, workspace)) == []

    def test_a_check_is_produced_for_every_service_either_way(self, workspace: RunWorkspace):
        complete(workspace)

        checks = check_contract(derive_contract(architecture()), {}, workspace)

        assert [check.service for check in checks] == [BACKEND_SLUG, FRONTEND_SLUG]
        assert {check.name for check in checks} == {CHECK_NAME}


class TestMissingServiceDirectory:
    def test_a_service_nobody_generated_is_reported(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py", "app/models.py")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert len(failures) == 1
        assert "no source was generated" in failures[0]

    def test_it_names_the_service(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py", "app/models.py")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert failures[0].startswith(f"{FRONTEND_SLUG}:")

    def test_its_missing_files_are_not_also_listed(self, workspace: RunWorkspace):
        """One fact, not a dozen consequences of it."""
        build(workspace, BACKEND, "app/main.py", "app/models.py")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert not any("src/index.jsx" in failure for failure in failures)

    def test_an_empty_directory_does_not_count_as_generated(self, workspace: RunWorkspace):
        complete(workspace)
        workspace.service_source("Reports").mkdir(parents=True, exist_ok=True)
        document = architecture()
        document["services"].append({"name": "Reports", "description": "Nothing here."})

        failures = failures_of(check_contract(derive_contract(document), {}, workspace))

        assert any("reports" in failure and "no source" in failure for failure in failures)


class TestMissingFiles:
    def test_a_missing_key_file_is_reported(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py")
        build(workspace, FRONTEND, "src/index.jsx")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert failures == [
            f"{BACKEND_SLUG}/app/models.py: the architecture requires this key file, "
            f"which the generated code does not have"
        ]

    def test_a_missing_entrypoint_says_so_by_name(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/models.py")
        build(workspace, FRONTEND, "src/index.jsx")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert "requires this entrypoint" in failures[0]
        assert "app/main.py" in failures[0]

    def test_a_missing_folder_is_reported(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "main.py", "models.py")
        build(workspace, FRONTEND, "src/index.jsx")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert any("app: the architecture requires this folder" in f for f in failures)

    def test_a_directory_does_not_satisfy_a_required_file(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py/placeholder.txt", "app/models.py")
        build(workspace, FRONTEND, "src/index.jsx")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert any("app/main.py" in failure for failure in failures)

    def test_a_file_does_not_satisfy_a_required_folder(self, workspace: RunWorkspace):
        document = architecture()
        document["project_structure"][1]["folders"] = ["src/components"]
        build(workspace, BACKEND, "app/main.py", "app/models.py")
        build(workspace, FRONTEND, "src/index.jsx", "src/components")

        failures = failures_of(check_contract(derive_contract(document), {}, workspace))

        assert any("src/components" in failure for failure in failures)


class TestDanglingDependencies:
    def test_a_dependency_on_a_service_that_does_not_exist_is_reported(
        self, workspace: RunWorkspace
    ):
        document = architecture()
        document["services"][1]["dependencies"] = ["Reporting Service"]
        complete(workspace)

        failures = failures_of(check_contract(derive_contract(document), {}, workspace))

        assert any("reporting-service" in failure for failure in failures)

    def test_a_dependency_on_a_declared_service_is_fine(self, workspace: RunWorkspace):
        complete(workspace)

        assert failures_of(check_contract(derive_contract(architecture()), {}, workspace)) == []


class TestViolationsIdentifyThemselves:
    def test_every_violation_names_its_service(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py")

        checks = check_contract(derive_contract(architecture()), {}, workspace)

        for check in checks:
            for failure in check.failures:
                assert failure.startswith(f"{check.service}/") or failure.startswith(
                    f"{check.service}:"
                )

    def test_a_file_violation_names_the_path(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py")
        build(workspace, FRONTEND, "src/index.jsx")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert f"{BACKEND_SLUG}/app/models.py" in failures[0]

    def test_every_violation_gives_a_reason(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py")

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert failures
        assert all("the architecture" in failure for failure in failures)

    def test_the_check_carries_the_canonical_slug(self, workspace: RunWorkspace):
        complete(workspace)

        checks = check_contract(derive_contract(architecture()), {}, workspace)

        assert [check.service for check in checks] == [BACKEND_SLUG, FRONTEND_SLUG]


class TestNothingUncheckableIsReported:
    """Silence is the correct answer for anything a path lookup cannot settle.

    A false violation costs a developer pass and the tokens to pay for it, so
    endpoints and data models are recorded in the contract and left alone. A
    router prefix legitimately splits a path in two, a parameter name is a free
    choice, and a frontend may call an endpoint rather than serve it — each of
    those is ordinary correct code that substring matching would condemn.
    """

    def test_an_unimplemented_endpoint_is_not_a_violation(self, workspace: RunWorkspace):
        complete(workspace)

        assert failures_of(check_contract(derive_contract(architecture()), {}, workspace)) == []

    def test_an_absent_data_model_is_not_a_violation(self, workspace: RunWorkspace):
        """`Expense` appears nowhere in the generated source, and that is allowed."""
        complete(workspace)

        failures = failures_of(check_contract(derive_contract(architecture()), {}, workspace))

        assert not any("Expense" in failure for failure in failures)

    def test_a_service_the_architecture_did_not_describe_is_not_faulted(
        self, workspace: RunWorkspace
    ):
        complete(workspace)
        document = architecture()
        del document["project_structure"][0]

        failures = failures_of(check_contract(derive_contract(document), {}, workspace))

        assert failures == []

    def test_no_contract_means_no_checks(self, workspace: RunWorkspace):
        assert check_contract(None, {}, workspace) == []
        assert check_contract({}, {}, workspace) == []

    def test_a_contract_that_cannot_be_read_is_skipped_rather_than_failed(
        self, workspace: RunWorkspace
    ):
        assert check_contract({"services": "not a list"}, {}, workspace) == []

    def test_no_workspace_means_no_checks(self):
        assert check_contract(derive_contract(architecture()), {}, None) == []


class TestManifestAgreement:
    def test_a_service_on_disk_but_not_in_the_manifest_is_reported(
        self, workspace: RunWorkspace
    ):
        complete(workspace)
        manifest = {BACKEND_SLUG: {"display_name": BACKEND, "files": []}}

        failures = failures_of(check_contract(derive_contract(architecture()), manifest, workspace))

        assert any("manifest has no record" in failure for failure in failures)

    def test_an_empty_manifest_makes_no_such_claim(self, workspace: RunWorkspace):
        """Nothing recorded yet is not the same as a disagreement."""
        complete(workspace)

        assert failures_of(check_contract(derive_contract(architecture()), {}, workspace)) == []


# ── Through the static gate ──────────────────────────────────────


class TestThroughTheStaticGate:
    def test_violations_land_in_the_static_report(self, workspace: RunWorkspace):
        build(workspace, BACKEND, "app/main.py")

        report = run_static_gate(workspace, None, None, derive_contract(architecture()).model_dump())

        assert not report.passed
        assert any("models.py" in failure for failure in report.failures)

    def test_a_complete_project_still_passes(self, workspace: RunWorkspace):
        complete(workspace)

        report = run_static_gate(workspace, None, None, derive_contract(architecture()).model_dump())

        assert report.passed

    def test_compiler_output_comes_before_design_mismatches(self, workspace: RunWorkspace):
        workspace.write_source_file(BACKEND, "app/main.py", fakes.BROKEN_SOURCE)
        build(workspace, FRONTEND, "src/index.jsx")

        report = run_static_gate(workspace, None, None, derive_contract(architecture()).model_dump())

        syntax = next(i for i, f in enumerate(report.failures) if "never compiles" in f or ":" in f)
        contract = next(i for i, f in enumerate(report.failures) if "the architecture" in f)
        assert syntax < contract

    def test_a_declared_service_with_no_directory_is_caught(self, workspace: RunWorkspace):
        """Nothing else in the gate can see a service that was never generated."""
        report = run_static_gate(
            workspace, None, None, derive_contract(architecture()).model_dump()
        )

        assert report.ran
        assert len(report.failures) == 2

    def test_without_a_contract_the_gate_is_exactly_as_before(self, workspace: RunWorkspace):
        complete(workspace)

        with_none = run_static_gate(workspace)
        with_empty = run_static_gate(workspace, None, None, {})

        assert with_none.model_dump() == with_empty.model_dump()

    def test_an_empty_workspace_still_reports_no_sources_found(self, workspace: RunWorkspace):
        report = run_static_gate(workspace)

        assert report.ran is False
        assert report.checks[0].skip_reason == "No generated source directories were found."

    def test_the_report_is_still_a_static_report(self, workspace: RunWorkspace):
        complete(workspace)

        report = run_static_gate(workspace, None, None, derive_contract(architecture()).model_dump())

        assert isinstance(report, StaticReport)


# ── Attribution ──────────────────────────────────────────────────


class TestViolationsAreAttributable:
    """Phase 1's mapping consumes these without being told they are new.

    The check carries the canonical slug, which is the same identity the manifest
    and the workspace use, so no second service-identity mechanism appears.
    """

    def _report(self, workspace: RunWorkspace) -> dict[str, Any]:
        build(workspace, BACKEND, "app/main.py")
        return run_static_gate(
            workspace, None, None, derive_contract(architecture()).model_dump()
        ).model_dump(mode="json")

    def test_each_violation_reaches_its_service(self, workspace: RunWorkspace):
        mapped = attribution_map(static_report=self._report(workspace))

        assert BACKEND_SLUG in mapped
        assert FRONTEND_SLUG in mapped

    def test_a_missing_file_is_attributed_to_the_service_that_owed_it(
        self, workspace: RunWorkspace
    ):
        mapped = attribution_map(static_report=self._report(workspace))

        assert any("app/models.py" in line for line in mapped[BACKEND_SLUG])
        assert not any("app/models.py" in line for line in mapped[FRONTEND_SLUG])

    def test_a_missing_service_is_attributed_to_itself(self, workspace: RunWorkspace):
        mapped = attribution_map(static_report=self._report(workspace))

        assert any("no source was generated" in line for line in mapped[FRONTEND_SLUG])

    def test_both_services_are_implicated(self, workspace: RunWorkspace):
        assert implicated_services(static_report=self._report(workspace)) == [
            BACKEND_SLUG,
            FRONTEND_SLUG,
        ]

    def test_nothing_lands_unattributed(self, workspace: RunWorkspace):
        from agents.attribution import UNATTRIBUTED

        assert UNATTRIBUTED not in attribution_map(static_report=self._report(workspace))
