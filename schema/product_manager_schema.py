from pydantic import BaseModel, Field

from schema.enums import LenientStrEnum


class Priority(LenientStrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ComplexityLevel(LenientStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Feature(BaseModel):
    name: str
    description: str
    priority: Priority
    is_mvp: bool = False
    acceptance_criteria: list[str] = Field(default_factory=list)


class DataEntity(BaseModel):
    name: str
    description: str
    key_attributes: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class APIEndpoint(BaseModel):
    method: str
    path: str
    description: str
    auth_required: bool = True
    related_module: str | None = None


class UserFlow(BaseModel):
    name: str
    actor: str
    steps: list[str]
    related_features: list[str] = Field(default_factory=list)


class Module(BaseModel):
    name: str
    responsibility: str
    exposes: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(BaseModel):
    category: str
    description: str
    target_metric: str | None = None


class ManagerSchema(BaseModel):

    # ── Product Overview ──────────────────────────────────────────────
    product_name: str
    product_summary: str
    problem_statement: str
    target_users: list[str]
    success_metrics: list[str] = Field(default_factory=list)

    # ── Features ─────────────────────────────────────────────────────
    features: list[Feature]

    # ── User Flows ───────────────────────────────────────────────────
    user_flows: list[UserFlow] = Field(default_factory=list)

    # ── Architecture Hints ────────────────────────────────────────────
    modules: list[Module]
    suggested_tech_stack: list[str] = Field(default_factory=list)
    expected_scale: str | None = Field(
        default=None,
        description="e.g. '10k DAU at launch, 1M DAU in year 1'"
    )

    # ── Data Layer ───────────────────────────────────────────────────
    data_entities: list[DataEntity]

    # ── API Surface ──────────────────────────────────────────────────
    possible_apis: list[APIEndpoint]

    # ── Requirements ─────────────────────────────────────────────────
    functional_requirements: list[str]
    non_functional_requirements: list[NonFunctionalRequirement]

    # ── Constraints & Assumptions ─────────────────────────────────────
    constraints: list[str]
    assumptions: list[str]

    # ── Handoff Metadata ──────────────────────────────────────────────
    open_questions: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    complexity_estimate: ComplexityLevel = ComplexityLevel.MEDIUM
