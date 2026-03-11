from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ComplexityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Feature(BaseModel):
    name: str
    description: str
    priority: Priority
    is_mvp: bool = False
    acceptance_criteria: List[str] = Field(default_factory=list)


class DataEntity(BaseModel):
    name: str
    description: str
    key_attributes: List[str] = Field(default_factory=list)
    relationships: List[str] = Field(default_factory=list)


class APIEndpoint(BaseModel):
    method: str
    path: str
    description: str
    auth_required: bool = True
    related_module: Optional[str] = None


class UserFlow(BaseModel):
    name: str
    actor: str
    steps: List[str]
    related_features: List[str] = Field(default_factory=list)


class Module(BaseModel):
    name: str
    responsibility: str
    exposes: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)


class NonFunctionalRequirement(BaseModel):
    category: str
    description: str
    target_metric: Optional[str] = None


class ManagerSchema(BaseModel):

    # ── Product Overview ──────────────────────────────────────────────
    product_name: str
    product_summary: str
    problem_statement: str
    target_users: List[str]
    success_metrics: List[str] = Field(default_factory=list)

    # ── Features ─────────────────────────────────────────────────────
    features: List[Feature]

    # ── User Flows ───────────────────────────────────────────────────
    user_flows: List[UserFlow] = Field(default_factory=list)

    # ── Architecture Hints ────────────────────────────────────────────
    modules: List[Module]
    suggested_tech_stack: List[str] = Field(default_factory=list)
    expected_scale: Optional[str] = Field(
        default=None,
        description="e.g. '10k DAU at launch, 1M DAU in year 1'"
    )

    # ── Data Layer ───────────────────────────────────────────────────
    data_entities: List[DataEntity]

    # ── API Surface ──────────────────────────────────────────────────
    possible_apis: List[APIEndpoint]

    # ── Requirements ─────────────────────────────────────────────────
    functional_requirements: List[str]
    non_functional_requirements: List[NonFunctionalRequirement]

    # ── Constraints & Assumptions ─────────────────────────────────────
    constraints: List[str]
    assumptions: List[str]

    # ── Handoff Metadata ──────────────────────────────────────────────
    open_questions: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    complexity_estimate: ComplexityLevel = ComplexityLevel.MEDIUM