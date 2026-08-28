from pydantic import BaseModel, Field

from schema.enums import LenientStrEnum

# ─────────────────────────────────────────────
# Architecture Style
# ─────────────────────────────────────────────

class ArchitectureStyle(LenientStrEnum):
    MONOLITH = "monolith"
    MODULAR_MONOLITH = "modular_monolith"
    MICROSERVICES = "microservices"
    EVENT_DRIVEN = "event_driven"


# ─────────────────────────────────────────────
# API Definition
# ─────────────────────────────────────────────

class APIEndpoint(BaseModel):
    method: str
    path: str
    description: str
    request_body: str | None = None
    response: str | None = None
    auth_required: bool = True


# ─────────────────────────────────────────────
# Database Table / Model
# ─────────────────────────────────────────────

class DataModel(BaseModel):
    name: str
    description: str
    fields: list[str]


# ─────────────────────────────────────────────
# Service Definition
# ─────────────────────────────────────────────

class Service(BaseModel):

    name: str = Field(..., description="Service name")

    description: str = Field(
        ..., description="Responsibility of the service"
    )

    tech_stack: list[str] = Field(
        ..., description="Technologies used in the service"
    )

    dependencies: list[str] = Field(
        default_factory=list,
        description="Other services this service depends on"
    )

    api_endpoints: list[APIEndpoint] = Field(
        default_factory=list,
        description="API endpoints exposed by the service"
    )

    data_models: list[DataModel] = Field(
        default_factory=list,
        description="Database models used by the service"
    )


# ─────────────────────────────────────────────
# Database Definition
# ─────────────────────────────────────────────

class Database(BaseModel):

    name: str

    type: str = Field(
        ...,
        description="Database type such as PostgreSQL, MongoDB, Redis"
    )

    purpose: str

    entities: list[str]


# ─────────────────────────────────────────────
# External Service Integration
# ─────────────────────────────────────────────

class ExternalIntegration(BaseModel):

    name: str
    purpose: str
    integration_method: str = Field(
        ...,
        description="REST API, SDK, webhook etc"
    )


# ─────────────────────────────────────────────
# Environment Variables
# ─────────────────────────────────────────────

class EnvironmentVariable(BaseModel):

    name: str
    description: str
    example: str | None = None


# ─────────────────────────────────────────────
# Project Folder Structure
# ─────────────────────────────────────────────

class ProjectStructure(BaseModel):

    service_name: str

    folders: list[str] = Field(
        ..., description="List of folders that must exist"
    )

    key_files: list[str] = Field(
        ..., description="Important files developer must implement"
    )


# ─────────────────────────────────────────────
# Implementation Tasks
# ─────────────────────────────────────────────

class ImplementationTask(BaseModel):

    service: str
    task: str
    description: str


# ─────────────────────────────────────────────
# Docker Configuration
# ─────────────────────────────────────────────

class DockerService(BaseModel):

    name: str
    image_or_build: str
    ports: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class DockerCompose(BaseModel):

    services: list[DockerService]


# ─────────────────────────────────────────────
# MAIN ARCHITECT SCHEMA
# ─────────────────────────────────────────────

class ArchitectSchema(BaseModel):

    system_overview: str = Field(
        ..., description="High level architecture explanation"
    )

    architecture_style: ArchitectureStyle

    services: list[Service] = Field(
        ..., description="Core services to implement"
    )

    databases: list[Database] = Field(
        default_factory=list,
        description="Databases used in the system"
    )

    external_integrations: list[ExternalIntegration] = Field(
        default_factory=list,
        description="Third party APIs or services"
    )

    environment_variables: list[EnvironmentVariable] = Field(
        default_factory=list,
        description="Required environment variables"
    )

    project_structure: list[ProjectStructure] = Field(
        default_factory=list,
        description="Folder structure each service must follow"
    )

    docker_compose: DockerCompose | None = Field(
        default=None,
        description="Local docker compose deployment config"
    )

    implementation_tasks: list[ImplementationTask] = Field(
        default_factory=list,
        description="Tasks developer agents should execute"
    )

    development_notes: list[str] = Field(
        default_factory=list,
        description="Important notes for developers"
    )

    risks: list[str] = Field(
        default_factory=list,
        description="Technical risks in the architecture"
    )

    complexity_estimate: str = Field(
        default="medium"
    )
