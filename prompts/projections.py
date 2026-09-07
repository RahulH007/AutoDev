"""What each agent is shown of the document before it.

Every agent used to receive both documents whole. That is generous and it was
never affordable: on a measured run the developer prompt reached 8,134 tokens
against an 8,000 token-per-minute ceiling, and the request was refused before it
left the process.

These are *projections*, not edits. The artifacts on disk and the graph state
keep every field the model produced; only the copy embedded in a prompt is
narrowed. Nothing here rewrites a value — a key is either copied verbatim or
left out.

The selections below are not taste. Each list is derived from what the receiving
agent has to produce:

- The architect fills an ``ArchitectSchema``, so it needs whatever feeds one of
  its fields — features and modules become services, data entities become data
  models and databases, possible APIs become endpoints. It does not need the
  PM's reasoning (``assumptions``), the questions left for a human
  (``open_questions``), product-level measures (``success_metrics``), or the
  background of why the product exists (``problem_statement``) — the summary carries
  enough context to design from.

- The developer implements the architecture, not the PRD. Anything the architect
  already decided is dropped, because the architecture states it authoritatively:
  ``possible_apis`` was a suggestion and ``api_endpoints`` is the decision,
  ``modules`` was a sketch and ``services`` is the shape. What remains is what
  the code must satisfy — the features, the entities, the behaviour and the
  constraints.
"""

from __future__ import annotations

from typing import Any

# Feeds a field of ArchitectSchema, directly or by informing a choice within one.
ARCHITECT_PRD_FIELDS = (
    "product_name",
    "product_summary",
    "target_users",
    "features",
    "user_flows",
    "modules",
    "suggested_tech_stack",
    "expected_scale",
    "data_entities",
    "possible_apis",
    "functional_requirements",
    "non_functional_requirements",
    "constraints",
    "out_of_scope",
    "complexity_estimate",
)

# What source code has to satisfy. Everything the architecture already settled
# is left to the architecture.
DEVELOPER_PRD_FIELDS = (
    "product_name",
    "product_summary",
    "features",
    "data_entities",
    "functional_requirements",
    "constraints",
)

# Project-level architecture: true for every service, so a later per-service pass
# sends this once and one service alongside it.
ARCHITECTURE_PREAMBLE_FIELDS = (
    "architecture_style",
    "system_overview",
    "databases",
    "external_integrations",
    "environment_variables",
    "project_structure",
    "docker_compose",
    "development_notes",
)


def _project(document: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    """Copy the named fields that carry something.

    An absent field and an empty one are treated alike: neither tells the model
    anything, and an empty list still costs tokens and an entry to read past.
    """
    source = document or {}
    return {
        field: source[field]
        for field in fields
        if field in source and source[field] not in (None, "", [], {})
    }


def prd_for_architect(prd: dict[str, Any] | None) -> dict[str, Any]:
    """The PRD as the architect needs it: the specification, not the reasoning."""
    return _project(prd, ARCHITECT_PRD_FIELDS)


def prd_for_developer(prd: dict[str, Any] | None) -> dict[str, Any]:
    """The PRD as the developer needs it: what the code must satisfy."""
    return _project(prd, DEVELOPER_PRD_FIELDS)


def architecture_preamble(architecture: dict[str, Any] | None) -> dict[str, Any]:
    """Project-level architecture, without the services.

    ``implementation_tasks`` and ``risks`` are left out on purpose: they are the
    architect's plan for whoever schedules the work, and the developer writes
    every service in one pass regardless of the order they suggest.
    """
    return _project(architecture, ARCHITECTURE_PREAMBLE_FIELDS)


def architecture_services(architecture: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The services, untouched and in order.

    Separate from the preamble so a per-service developer pass can send one at a
    time without any further reshaping.
    """
    return list((architecture or {}).get("services") or [])
