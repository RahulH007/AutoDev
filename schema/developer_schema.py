from pydantic import BaseModel, Field
from typing import List, Optional

class CodeFile(BaseModel):
    file_path: str = Field(..., description="Relative path including filename, e.g., 'app/main.py'")
    file_name: str = Field(..., description="Just the filename, e.g., 'main.py'")
    language: str = Field(..., description="Programming language or format (e.g., python, javascript, json)")
    code: str = Field(..., description="The complete, runnable source code")
    description: str = Field(..., description="Brief description of what this file does")

class ServiceCode(BaseModel):
    service_name: str = Field(..., description="Name of the service this code belongs to")
    files: List[CodeFile] = Field(..., description="All source code files for this service")

class DeveloperSchema(BaseModel):
    project_name: str = Field(..., description="Name of the project")
    services: List[ServiceCode] = Field(..., description="Code organized by service")
    setup_instructions: List[str] = Field(
        default_factory=list,
        description="Step-by-step instructions on how to set up and run the code locally"
    )
    dependency_files: List[CodeFile] = Field(
        default_factory=list,
        description="Global or shared dependency files (e.g., requirements.txt at the root)"
    )
    development_notes: List[str] = Field(
        default_factory=list,
        description="Notes from the developer about implementation details or limitations"
    )
    readme_content: str = Field(
        default="",
        description="Complete README.md content in markdown format. Must include: project overview, architecture summary, services description, prerequisites, step-by-step setup and run instructions, environment variables, API endpoints, and development notes."
    )
