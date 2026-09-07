"""What each agent is actually shown of the documents before it.

Every agent was handed both documents whole. That is 3,057 tokens of PRD for a
developer that works from the architecture, against an 8,000 token-per-minute
ceiling the developer stage had already broken.

These are projections, not edits: the artifacts on disk and the graph state keep
every field. Only the copy embedded in a prompt is narrowed, so the tests below
check twice — that the needed fields survive, and that the source is untouched.
"""

from __future__ import annotations

import copy

from prompts.projections import (
    architecture_preamble,
    architecture_services,
    prd_for_architect,
    prd_for_developer,
)

PRD = {
    "product_name": "SpendWise",
    "product_summary": "Track expenses.",
    "problem_statement": "People lose receipts.",
    "target_users": ["freelancers"],
    "success_metrics": ["weekly actives"],
    "features": [{"name": "Login", "priority": "high", "is_mvp": True}],
    "user_flows": ["sign in then add an expense"],
    "modules": ["auth", "expenses"],
    "suggested_tech_stack": ["Python"],
    "expected_scale": "small",
    "data_entities": [{"name": "Expense", "fields": ["amount"]}],
    "possible_apis": [{"method": "POST", "path": "/expenses"}],
    "functional_requirements": ["A user can create an expense"],
    "non_functional_requirements": ["p95 under 300ms"],
    "constraints": ["No third-party accounting integrations"],
    "assumptions": ["One currency"],
    "open_questions": ["Do we need receipts?"],
    "out_of_scope": ["Mobile app"],
    "complexity_estimate": "medium",
}

ARCHITECTURE = {
    "architecture_style": "modular_monolith",
    "system_overview": "One FastAPI service and a React frontend.",
    "services": [
        {"name": "backend", "tech_stack": ["FastAPI"], "api_endpoints": [{"path": "/x"}]},
        {"name": "frontend", "tech_stack": ["React"], "api_endpoints": []},
    ],
    "databases": [{"name": "app_db", "type": "postgres", "purpose": "Everything"}],
    "external_integrations": [{"name": "Stripe", "integration_method": "REST"}],
    "environment_variables": [{"name": "DATABASE_URL", "description": "connection"}],
    "project_structure": [{"service": "backend", "folders": ["app"]}],
    "docker_compose": {"services": [{"name": "backend"}]},
    "implementation_tasks": [{"service": "backend", "task": "Build auth"}],
    "development_notes": ["Use Alembic"],
    "risks": ["Scope creep"],
    "complexity_estimate": "medium",
}


def _is_faithful_subset(projection: dict, source: dict) -> bool:
    """Every key kept must carry exactly the value the source had."""
    return all(key in source and projection[key] == source[key] for key in projection)


class TestProjectionsAreSubsetsNotEdits:
    def test_the_architect_projection_only_copies(self):
        assert _is_faithful_subset(prd_for_architect(PRD), PRD)

    def test_the_developer_prd_projection_only_copies(self):
        assert _is_faithful_subset(prd_for_developer(PRD), PRD)

    def test_the_architecture_preamble_only_copies(self):
        assert _is_faithful_subset(architecture_preamble(ARCHITECTURE), ARCHITECTURE)

    def test_the_source_documents_are_never_mutated(self):
        """The same dict is the graph state; projecting must not touch it."""
        prd, architecture = copy.deepcopy(PRD), copy.deepcopy(ARCHITECTURE)

        prd_for_architect(prd)
        prd_for_developer(prd)
        architecture_preamble(architecture)
        architecture_services(architecture)

        assert prd == PRD
        assert architecture == ARCHITECTURE

    def test_a_document_missing_fields_is_handled(self):
        """A model may omit an optional field; a projection must not invent it."""
        assert prd_for_architect({"product_name": "X"}) == {"product_name": "X"}
        assert prd_for_developer({}) == {}
        assert architecture_preamble({}) == {}
        assert architecture_services({}) == []

    def test_empty_values_are_dropped_rather_than_sent_as_noise(self):
        """An empty list costs tokens and tells the model nothing."""
        projected = prd_for_architect({"product_name": "X", "features": [], "modules": None})
        assert "features" not in projected
        assert "modules" not in projected


class TestArchitectSeesWhatItMustProduceFrom:
    """Every ArchitectSchema field has to be derivable from what we send."""

    def test_it_keeps_the_fields_that_drive_the_schema(self):
        projected = prd_for_architect(PRD)
        for field in (
            "features",             # -> services
            "modules",              # -> service decomposition
            "data_entities",        # -> data_models, databases
            "possible_apis",        # -> api_endpoints
            "functional_requirements",
            "non_functional_requirements",  # -> caching, scale, security choices
            "constraints",
            "suggested_tech_stack",  # -> tech_stack
            "expected_scale",
            "user_flows",
            "out_of_scope",
        ):
            assert field in projected, f"architect needs {field}"

    def test_it_drops_what_feeds_no_schema_field(self):
        projected = prd_for_architect(PRD)
        for field in ("open_questions", "assumptions", "success_metrics", "problem_statement"):
            assert field not in projected

    def test_it_is_smaller_than_the_whole_document(self):
        assert len(prd_for_architect(PRD)) < len(PRD)


class TestDeveloperSeesOnlyWhatItImplements:
    def test_it_keeps_what_the_code_is_written_from(self):
        projected = prd_for_developer(PRD)
        for field in ("product_name", "features", "data_entities",
                      "functional_requirements", "constraints"):
            assert field in projected

    def test_it_drops_what_the_architecture_already_decided(self):
        """possible_apis and modules are the PM's suggestions; the architecture
        carries the real api_endpoints and services that supersede them."""
        projected = prd_for_developer(PRD)
        for field in ("possible_apis", "modules", "non_functional_requirements",
                      "open_questions", "assumptions", "problem_statement",
                      "target_users", "success_metrics"):
            assert field not in projected

    def test_it_is_smaller_than_the_architect_projection(self):
        assert len(prd_for_developer(PRD)) < len(prd_for_architect(PRD))


class TestArchitecturePreambleAndServicesSplit:
    def test_the_preamble_carries_project_level_facts_only(self):
        preamble = architecture_preamble(ARCHITECTURE)
        for field in ("architecture_style", "system_overview", "databases",
                      "environment_variables", "project_structure",
                      "docker_compose", "development_notes"):
            assert field in preamble

    def test_the_preamble_excludes_the_services(self):
        """Kept separate so a later per-service pass can send one at a time."""
        assert "services" not in architecture_preamble(ARCHITECTURE)

    def test_the_preamble_drops_planning_notes_the_coder_does_not_need(self):
        preamble = architecture_preamble(ARCHITECTURE)
        assert "implementation_tasks" not in preamble
        assert "risks" not in preamble

    def test_services_come_back_whole_and_in_order(self):
        services = architecture_services(ARCHITECTURE)
        assert [s["name"] for s in services] == ["backend", "frontend"]
        assert services == ARCHITECTURE["services"]

    def test_preamble_plus_services_drops_only_planning_fields(self):
        """Everything the architecture states about the software itself survives;
        only the fields aimed at a human planning the work are left out."""
        sent = set(architecture_preamble(ARCHITECTURE)) | {"services"}
        dropped = set(ARCHITECTURE) - sent

        assert dropped == {"implementation_tasks", "risks", "complexity_estimate"}
