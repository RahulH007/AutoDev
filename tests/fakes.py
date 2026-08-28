"""Canned agent outputs and fake LLMs.

Every builder returns a fully valid instance of the real Pydantic contract, so the
pipeline can be driven end to end with no API calls and no network. The fake
developer output is a small pure-stdlib Python project: it genuinely compiles and
its tests genuinely pass, which lets the verification layer be tested for real.
"""

from __future__ import annotations

import json
from typing import Any

from schema.architect_schema import (
    APIEndpoint as ArchEndpoint,
)
from schema.architect_schema import (
    ArchitectSchema,
    ArchitectureStyle,
    Database,
    DataModel,
    DockerCompose,
    DockerService,
    EnvironmentVariable,
    ImplementationTask,
    ProjectStructure,
    Service,
)
from schema.developer_schema import CodeFile, DeveloperSchema, ServiceCode
from schema.product_manager_schema import (
    APIEndpoint as PmEndpoint,
)
from schema.product_manager_schema import (
    ComplexityLevel,
    DataEntity,
    Feature,
    ManagerSchema,
    Module,
    NonFunctionalRequirement,
    Priority,
    UserFlow,
)
from schema.qa_schema import Bug, QASchema, QAServiceReport, Severity, TestCase

# The service name is deliberately human-formatted with a space and capitals so
# tests exercise slug normalisation on the way to disk.
SERVICE_NAME = "Backend API"
SERVICE_SLUG = "backend-api"


# ─────────────────────────────────────────────────────────────────
# Generated project source
# ─────────────────────────────────────────────────────────────────

CALCULATOR_SOURCE = '''"""Arithmetic helpers."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("cannot divide by zero")
    return a / b
'''

STORE_SOURCE = '''"""In-memory record store."""

from typing import Any


class Store:
    def __init__(self) -> None:
        self._items: dict[str, Any] = {}

    def put(self, key: str, value: Any) -> None:
        self._items[key] = value

    def get(self, key: str) -> Any:
        return self._items.get(key)

    def all(self) -> dict[str, Any]:
        return dict(self._items)
'''

# Missing a closing parenthesis: py_compile must reject this.
BROKEN_SOURCE = '''def broken(:
    return "this never compiles"
'''

PASSING_TEST_SOURCE = '''from app.calculator import add, divide, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_divide():
    assert divide(6, 3) == 2


def test_divide_by_zero():
    try:
        divide(1, 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
'''

FAILING_TEST_SOURCE = '''from app.calculator import add


def test_add_is_wrong():
    assert add(2, 3) == 6
'''


# ─────────────────────────────────────────────────────────────────
# PM agent
# ─────────────────────────────────────────────────────────────────


def build_prd(product_name: str = "SpendWise") -> ManagerSchema:
    return ManagerSchema(
        product_name=product_name,
        product_summary="A personal finance tracker for logging and reviewing expenses.",
        problem_statement="People lose track of discretionary spending across accounts.",
        target_users=["Salaried professionals", "Freelancers"],
        success_metrics=["60% of users log an expense in week one"],
        features=[
            Feature(
                name="Expense logging",
                description="Record an expense with amount, category and date.",
                priority=Priority.HIGH,
                is_mvp=True,
                acceptance_criteria=["Amount must be positive", "Date defaults to today"],
            ),
            Feature(
                name="Monthly report",
                description="Summarise spending by category for a month.",
                priority=Priority.MEDIUM,
                is_mvp=True,
                acceptance_criteria=["Totals match the sum of logged expenses"],
            ),
        ],
        user_flows=[
            UserFlow(
                name="Log an expense",
                actor="Authenticated user",
                steps=["Open the app", "Tap add", "Enter amount and category", "Save"],
                related_features=["Expense logging"],
            )
        ],
        modules=[
            Module(
                name="expenses",
                responsibility="Owns expense persistence and validation.",
                exposes=["create_expense", "list_expenses"],
                depends_on=["auth"],
            )
        ],
        suggested_tech_stack=["Python", "FastAPI"],
        expected_scale="1k daily active users at launch",
        data_entities=[
            DataEntity(
                name="Expense",
                description="A single spending record.",
                key_attributes=["id", "user_id", "amount", "category", "spent_on"],
                relationships=["belongs to User"],
            )
        ],
        possible_apis=[
            PmEndpoint(
                method="POST",
                path="/api/v1/expenses",
                description="Create an expense.",
                auth_required=True,
                related_module="expenses",
            )
        ],
        functional_requirements=["Users can create, read and delete their own expenses."],
        non_functional_requirements=[
            NonFunctionalRequirement(
                category="Performance",
                description="List endpoints respond quickly under normal load.",
                target_metric="p95 under 300ms",
            )
        ],
        constraints=["Must run locally without cloud services."],
        assumptions=["A single currency is sufficient for the first release."],
        open_questions=["Should recurring expenses be in scope?"],
        out_of_scope=["Bank account synchronisation"],
        complexity_estimate=ComplexityLevel.MEDIUM,
    )


# ─────────────────────────────────────────────────────────────────
# Architecture agent
# ─────────────────────────────────────────────────────────────────


def build_architecture() -> ArchitectSchema:
    return ArchitectSchema(
        system_overview="A modular monolith exposing a REST API over a relational store.",
        architecture_style=ArchitectureStyle.MODULAR_MONOLITH,
        services=[
            Service(
                name=SERVICE_NAME,
                description="Serves the REST API and owns all business logic.",
                tech_stack=["Python", "FastAPI"],
                dependencies=[],
                api_endpoints=[
                    ArchEndpoint(
                        method="POST",
                        path="/api/v1/expenses",
                        description="Create an expense.",
                        request_body="ExpenseCreate",
                        response="ExpenseRead",
                        auth_required=True,
                    )
                ],
                data_models=[
                    DataModel(
                        name="Expense",
                        description="A spending record.",
                        fields=["id: int", "amount: float", "category: str"],
                    )
                ],
            )
        ],
        databases=[
            Database(
                name="primary",
                type="PostgreSQL",
                purpose="Stores users and expenses.",
                entities=["User", "Expense"],
            )
        ],
        environment_variables=[
            EnvironmentVariable(
                name="DATABASE_URL",
                description="Connection string for the primary database.",
                example="postgresql://postgres:postgres@localhost:5432/spendwise",
            )
        ],
        project_structure=[
            ProjectStructure(
                service_name=SERVICE_NAME,
                folders=["app"],
                key_files=["app/calculator.py", "app/store.py"],
            )
        ],
        docker_compose=DockerCompose(
            services=[DockerService(name="api", image_or_build="./backend-api", ports=["8000:8000"])]
        ),
        implementation_tasks=[
            ImplementationTask(
                service=SERVICE_NAME,
                task="Implement expense endpoints",
                description="Create, list and delete expenses scoped to the current user.",
            )
        ],
        development_notes=["Keep business logic out of the route handlers."],
        risks=["Report queries may need indexes as data grows."],
        complexity_estimate="medium",
    )


# ─────────────────────────────────────────────────────────────────
# Developer agent
# ─────────────────────────────────────────────────────────────────


def build_developer_output(*, broken: bool = False) -> DeveloperSchema:
    """A small, real, dependency-free Python project.

    With ``broken=True`` one file contains a syntax error so the static gate has
    something genuine to reject.
    """
    files = [
        CodeFile(
            file_path="app/__init__.py",
            file_name="__init__.py",
            language="python",
            code="",
            description="Marks the app package.",
        ),
        CodeFile(
            file_path="app/calculator.py",
            file_name="calculator.py",
            language="python",
            code=BROKEN_SOURCE if broken else CALCULATOR_SOURCE,
            description="Arithmetic helpers.",
        ),
        CodeFile(
            file_path="app/store.py",
            file_name="store.py",
            language="python",
            code=STORE_SOURCE,
            description="In-memory record store.",
        ),
    ]

    return DeveloperSchema(
        project_name="SpendWise",
        services=[ServiceCode(service_name=SERVICE_NAME, files=files)],
        setup_instructions=["Create a virtual environment", "Install requirements", "Run the app"],
        dependency_files=[
            CodeFile(
                file_path="requirements.txt",
                file_name="requirements.txt",
                language="text",
                code="pytest==9.1.1\n",
                description="Runtime and test dependencies.",
            )
        ],
        development_notes=["Storage is in-memory for the first iteration."],
        readme_content="# SpendWise\n\nA personal finance tracker.\n",
    )


def build_traversal_developer_output() -> DeveloperSchema:
    """Developer output whose file path tries to escape the workspace."""
    return DeveloperSchema(
        project_name="Malicious",
        services=[
            ServiceCode(
                service_name=SERVICE_NAME,
                files=[
                    CodeFile(
                        file_path="../../../../escaped.py",
                        file_name="escaped.py",
                        language="python",
                        code="print('escaped')\n",
                        description="Attempts to write outside the workspace.",
                    )
                ],
            )
        ],
    )


# ─────────────────────────────────────────────────────────────────
# QA agent
# ─────────────────────────────────────────────────────────────────


def build_qa_report(
    *,
    score: int = 9,
    critical_issues: int = 0,
    failing_tests: bool = False,
) -> QASchema:
    bugs = []
    if critical_issues:
        bugs = [
            Bug(
                file_path="app/calculator.py",
                line_number="10",
                severity=Severity.CRITICAL,
                description="divide does not guard against a zero denominator.",
                suggested_fix="Raise ValueError when b == 0.",
            )
        ] * critical_issues

    test_source = FAILING_TEST_SOURCE if failing_tests else PASSING_TEST_SOURCE
    test_name = "test_add_is_wrong" if failing_tests else "test_calculator"

    return QASchema(
        overall_assessment="The implementation is small, readable and covered by tests.",
        service_reports=[
            QAServiceReport(
                service_name=SERVICE_NAME,
                bugs=bugs,
                test_cases=[
                    TestCase(
                        test_name=test_name,
                        test_file_path="test_calculator.py",
                        description="Exercises the arithmetic helpers.",
                        test_code=test_source,
                        target_file="app/calculator.py",
                    )
                ],
                code_quality_score=score,
            )
        ],
        critical_issues=critical_issues,
        total_bugs_found=len(bugs),
        total_tests_written=1,
        recommendations=["Add property-based tests for the arithmetic helpers."],
        passed=critical_issues == 0 and score >= 7,
    )


# ─────────────────────────────────────────────────────────────────
# Fake LLMs
# ─────────────────────────────────────────────────────────────────

TRIAGE_MARKER = "Return ONLY a valid JSON array"


class FakeStructuredLLM:
    """Stands in for ``llm.with_structured_output(schema)``."""

    def __init__(self, responses: list[Any], recorder: list[Any] | None = None) -> None:
        if not responses:
            raise ValueError("FakeStructuredLLM needs at least one response")
        self._responses = responses
        self._calls = 0
        self._recorder = recorder if recorder is not None else []

    @property
    def calls(self) -> int:
        return self._calls

    def _next(self, prompt: Any) -> Any:
        self._recorder.append(prompt)
        index = min(self._calls, len(self._responses) - 1)
        self._calls += 1

        response = self._responses[index]
        # A queued exception stands in for a stage that could not produce output,
        # which is how a failing provider looks to the agent above it.
        if isinstance(response, Exception):
            raise response
        return response

    def invoke(self, prompt: Any) -> Any:
        return self._next(prompt)

    async def ainvoke(self, prompt: Any) -> Any:
        return self._next(prompt)


class LLMStub:
    """Replaces the registry so the pipeline runs with no provider.

    A single :class:`FakeStructuredLLM` is held per schema, so responses queued for
    a schema are consumed across successive agent invocations. That is what makes
    retry behaviour testable: queue a broken output then a fixed one. Queue an
    exception instead to make that stage fail.
    """

    def __init__(self) -> None:
        self._by_schema: dict[type, FakeStructuredLLM] = {}
        self.prompts: list[Any] = []
        self.text_prompts: list[Any] = []

    def set(self, schema: type, *responses: Any) -> LLMStub:
        self._by_schema[schema] = FakeStructuredLLM(list(responses), recorder=self.prompts)
        return self

    def calls_for(self, schema: type) -> int:
        stub = self._by_schema.get(schema)
        return stub.calls if stub else 0

    def get_structured_llm(self, schema: type, purpose: Any = None, settings: Any = None) -> Any:
        if schema not in self._by_schema:
            raise AssertionError(
                f"No canned response registered for {schema.__name__}. Call stub.set({schema.__name__}, ...)"
            )
        return self._by_schema[schema]

    def get_text_llm(self, purpose: Any = None, settings: Any = None) -> Any:
        raise AssertionError("Use llm_call / allm_call in tests rather than the raw text runnable")

    def llm_call(self, prompt: Any, purpose: Any = None, settings: Any = None) -> str:
        self.text_prompts.append(prompt)
        return fake_text_response(prompt)

    async def allm_call(self, prompt: Any, purpose: Any = None, settings: Any = None) -> str:
        self.text_prompts.append(prompt)
        return fake_text_response(prompt)

    def install(self, monkeypatch: Any) -> LLMStub:
        from llm import registry

        monkeypatch.setattr(registry, "get_structured_llm", self.get_structured_llm)
        monkeypatch.setattr(registry, "get_text_llm", self.get_text_llm)
        monkeypatch.setattr(registry, "llm_call", self.llm_call)
        monkeypatch.setattr(registry, "allm_call", self.allm_call)
        return self


def fake_text_response(prompt: Any) -> str:
    """Plain-text responder covering both text uses: QA triage and PDF prose."""
    rendered = render_prompt(prompt)

    if TRIAGE_MARKER in rendered:
        return json.dumps([f"{SERVICE_SLUG}/app/calculator.py"])

    return "# Generated Document\n\n## 1. Overview\n\nThis document was produced by a fake LLM.\n"


def render_prompt(prompt: Any) -> str:
    """Flatten a prompt (string, message, or message list) for assertions."""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        return "\n".join(render_prompt(part) for part in prompt)
    content = getattr(prompt, "content", None)
    if content is not None:
        return render_prompt(content)
    return str(prompt)


def render_all(prompts: list[Any]) -> str:
    return "\n".join(render_prompt(p) for p in prompts)


_render = render_prompt
