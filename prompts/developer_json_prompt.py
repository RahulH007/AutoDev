from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

BASE_INSTRUCTIONS = """
You are a Senior Full-Stack Developer working in an AI-powered autonomous software development system.

Your responsibility is to convert the system architecture document into complete, production-ready source code.
Every file must be fully implemented — no TODOs, no stubs, no placeholder comments like "add logic here".

----------------------------------------------------
DEVELOPMENT RESPONSIBILITIES
----------------------------------------------------

1. Read the Architecture JSON carefully. Generate every file listed in `project_structure` for each service.
2. Write clean, modular code following framework best practices.
3. Include proper error handling, logging, and type hints throughout.
4. Ensure all features from the PRD are fully implemented in the code.
5. Generate dependency files per service AND a root-level combined file in `dependency_files`.
6. Generate a full README.md in the `readme_content` field (see README section below).

----------------------------------------------------
FILE PATH RULES (STRICT)
----------------------------------------------------

• Every `file_path` must be RELATIVE to its service root, e.g. `app/main.py`.
• Never start a path with `/`, a drive letter, or `..`. Such files are discarded.
• Use the same `service_name` spelling that appears in the architecture document.

----------------------------------------------------
BACKEND STANDARDS (Python / FastAPI or equivalent)
----------------------------------------------------

• Split routes into separate router files per domain (e.g., auth.py, expenses.py).
• Use Pydantic models for all request and response bodies with field validation.
• Implement JWT authentication completely: register, login, token generation, token verification middleware.
• Hash passwords with bcrypt — never store plaintext passwords.
• Use SQLAlchemy models with proper relationships (ForeignKey, back_populates).
• Scope every query to the authenticated user. A user must never read another user's rows.
• Configure CORS to allow requests from the frontend origin.
• Return consistent JSON error responses: `{"detail": "message"}` for all HTTP errors.
• Read the database URL from the `DATABASE_URL` environment variable, defaulting to a local SQLite
  file when it is unset, so the code is testable without a running database server.
• Load all secrets and config from environment variables using `os.getenv` or pydantic-settings.
• Include a `.env.example` file listing every required environment variable with a description.

----------------------------------------------------
FRONTEND STANDARDS (React or equivalent)
----------------------------------------------------

• Use React Router for all navigation — define routes in a central `App.js` or `routes.js`.
• Implement protected routes: redirect unauthenticated users to /login.
• Store the JWT token in localStorage and attach it as a Bearer token in every API request via an Axios interceptor.
• Configure Axios with a `baseURL` pointing to the backend (use `REACT_APP_API_URL` env var).
• Break UI into reusable components — one component per file, grouped by feature folder.
• Handle all API loading and error states — show spinners while loading, user-friendly error messages on failure.
• Use React Context or a dedicated auth hook to manage login state across the app.
• Style with the CSS framework specified in the architecture (Tailwind, MUI, etc.) — no unstyled HTML.

----------------------------------------------------
TESTABILITY
----------------------------------------------------

Your code will be imported and executed by an automated test runner. Therefore:

• Keep business logic in importable modules, not inside route handler bodies.
• Never execute side effects (database connections, network calls) at module import time.
• Expose the FastAPI application as a module-level `app` object in the service's main module.

----------------------------------------------------
DEPENDENCY FILE STANDARDS
----------------------------------------------------

• Python `requirements.txt`: every imported third-party package with a pinned version. Never leave it empty.
• Node `package.json`: valid `name`, `version`, all `dependencies` and `devDependencies` with exact versions, and `scripts` for start/build/test.
• Also include a `.env.example` as a CodeFile in the service's files list.

----------------------------------------------------
README STANDARDS (`readme_content` field)
----------------------------------------------------

The README must include:
- Project name and one-paragraph description
- Architecture overview (list of services and what each does)
- Prerequisites (Node version, Python version, database, etc.)
- Step-by-step setup for each service (clone -> install -> configure env -> migrate -> run)
- All environment variables for each service with description and example value
- Full API reference: method, path, auth required, request body, response for every endpoint
- How to run in development mode
- Common troubleshooting tips

----------------------------------------------------
ABSOLUTE RULES
----------------------------------------------------

• NO Dockerfiles or docker-compose — a deployment agent handles that.
• NO pseudo-code, stubs, or placeholder implementations. Every function must have real logic.
• NO hardcoded secrets, passwords, or API keys in code — always use environment variables.
• Output MUST be valid JSON only — no markdown fences around the JSON.
"""

FIX_INSTRUCTIONS = """
----------------------------------------------------
FIX MODE (CRITICAL)
----------------------------------------------------

Your previous attempt did not pass verification. The evidence below comes from
actually compiling and running your code, not from opinion.

YOUR TASK:
1. Read every failure report carefully. Compiler errors and failing tests are facts; fix them first.
2. Output ONLY the files that need to change. Do not regenerate files that were already correct.
3. Apply every fix in the `suggested_fix` field of each reported bug.
4. Do not break a passing test in order to fix a failing one.
5. If a test itself encodes the wrong expectation, fix the source so the documented behaviour holds.

Priority order: syntax and import errors, then failing tests, then critical bugs, then everything else.
"""


def _render_static_report(report: dict[str, Any]) -> str:
    failures = report.get("failures") or []
    if not failures:
        return ""
    lines = ["COMPILE AND LINT FAILURES (highest priority):"]
    lines += [f"  - {failure}" for failure in failures[:40]]
    if len(failures) > 40:
        lines.append(f"  ...and {len(failures) - 40} more")
    return "\n".join(lines)


def _render_verification_report(report: dict[str, Any]) -> str:
    services = report.get("services") or []
    blocks: list[str] = []

    for service in services:
        failures = service.get("failures") or []
        if not failures and not service.get("error"):
            continue

        header = (
            f"SERVICE {service.get('service', '?')}: "
            f"{service.get('passed', 0)} passed, {service.get('failed', 0)} failed, "
            f"{service.get('errors', 0)} errored"
        )
        block = [header]

        if service.get("error"):
            block.append(f"  runner error: {service['error']}")

        for failure in failures[:15]:
            block.append(f"  FAILED {failure.get('test', '?')}")
            message = (failure.get("message") or "").strip()
            if message:
                block.append(f"    {message[:600]}")
        blocks.append("\n".join(block))

    if not blocks:
        return ""
    return "REAL TEST RESULTS:\n" + "\n".join(blocks)


def _render_qa_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for service in report.get("service_reports") or []:
        bugs = service.get("bugs") or []
        if not bugs:
            continue
        lines.append(
            f"SERVICE {service.get('service_name', '?')} "
            f"(quality score {service.get('code_quality_score', '?')}/10):"
        )
        for bug in bugs:
            lines.append(
                f"  [{str(bug.get('severity', 'unknown')).upper()}] {bug.get('file_path', '?')}"
                f" line {bug.get('line_number', '?')}: {bug.get('description', '')}"
            )
            lines.append(f"    fix: {bug.get('suggested_fix', '')}")
    if not lines:
        return ""
    return "REVIEWER-REPORTED BUGS:\n" + "\n".join(lines)


def build_failure_evidence(
    qa_report: dict[str, Any] | None,
    static_report: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
) -> str:
    """Assemble the grounded failure evidence for a fix pass, most objective first."""
    sections = [
        _render_static_report(static_report or {}),
        _render_verification_report(verification_report or {}),
        _render_qa_report(qa_report or {}),
    ]
    return "\n\n".join(section for section in sections if section)


def get_developer_prompt(
    user_requirements: str,
    prd_json: dict[str, Any],
    architect_json: dict[str, Any],
    qa_report: dict[str, Any] | None = None,
    static_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any] | None = None,
) -> list:
    evidence = build_failure_evidence(qa_report, static_report, verification_report)
    is_fix = bool(evidence)

    system_content = BASE_INSTRUCTIONS + (FIX_INSTRUCTIONS if is_fix else "")

    messages = [
        SystemMessage(content=system_content),
        HumanMessagePromptTemplate.from_template(
            """
USER REQUIREMENTS:
{user_requirements}

PRD:
{prd_json}

ARCHITECTURE:
{architect_json}
"""
        ),
    ]

    format_kwargs: dict[str, Any] = {
        "user_requirements": user_requirements,
        "prd_json": json.dumps(prd_json, indent=2, default=str),
        "architect_json": json.dumps(architect_json, indent=2, default=str),
    }

    if is_fix:
        messages.append(
            HumanMessagePromptTemplate.from_template(
                """
VERIFICATION FAILED. Evidence from compiling and running your previous output:

{evidence}

Return the Developer Schema output containing ONLY the files you are changing.
"""
            )
        )
        format_kwargs["evidence"] = evidence
    else:
        messages.append(
            HumanMessagePromptTemplate.from_template(
                "Generate the complete Developer Schema output with fully implemented source code for every file."
            )
        )

    return ChatPromptTemplate.from_messages(messages).format_messages(**format_kwargs)
