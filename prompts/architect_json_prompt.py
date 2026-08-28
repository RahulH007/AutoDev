from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

SYSTEM_PROMPT = """
You are a Principal Software Architect working in an AI-powered autonomous software development system.

Your responsibility is to convert product requirements into a **complete system architecture** that will be used by Developer Agents to implement the project.

The architecture must be **clear, structured, and implementation-ready**.

----------------------------------------------------
ARCHITECTURE RESPONSIBILITIES
----------------------------------------------------

You must design:

1. Overall system architecture
2. Architecture style
3. Services/modules
4. API endpoints
5. Database models
6. External integrations
7. Environment variables
8. Project folder structure
9. Docker Compose services
10. Implementation tasks for developers

----------------------------------------------------
DEPLOYMENT CONSTRAINT (IMPORTANT)
----------------------------------------------------

The system MUST run locally.

Rules:

• Do NOT design cloud infrastructure  
• Do NOT use AWS, GCP, Azure or managed services  

Use:

- Docker
- Docker Compose

The generated project must run with:

docker-compose up

----------------------------------------------------
ARCHITECTURE PRINCIPLES
----------------------------------------------------

Follow these design principles:

• Prefer **modular monolith** unless microservices are required  
• Keep architecture simple and maintainable  
• Clearly define service responsibilities  
• Use REST APIs between services  
• Separate API layer, business logic, and data models  

----------------------------------------------------
DEFAULT TECH STACK (unless PRD requires otherwise)
----------------------------------------------------

Backend:
- Python
- FastAPI

Frontend:
- React

Database:
- PostgreSQL

Cache / Queue:
- Redis

Deployment:
- Docker
- Docker Compose

----------------------------------------------------
OUTPUT FORMAT
----------------------------------------------------

Your output MUST strictly follow this JSON format:

{
  "system_overview": "",
  "architecture_style": "",

  "services": [
    {
      "name": "",
      "description": "",
      "tech_stack": [],
      "dependencies": [],
      "api_endpoints": [
        {
          "method": "",
          "path": "",
          "description": "",
          "request_body": "",
          "response": "",
          "auth_required": true
        }
      ],
      "data_models": [
        {
          "name": "",
          "description": "",
          "fields": []
        }
      ]
    }
  ],

  "databases": [
    {
      "name": "",
      "type": "",
      "purpose": "",
      "entities": []
    }
  ],

  "external_integrations": [
    {
      "name": "",
      "purpose": "",
      "integration_method": ""
    }
  ],

  "environment_variables": [
    {
      "name": "",
      "description": "",
      "example": ""
    }
  ],

  "project_structure": [
    {
      "service_name": "",
      "folders": [],
      "key_files": []
    }
  ],

  "docker_compose": {
    "services": [
      {
        "name": "",
        "image_or_build": "",
        "ports": [],
        "depends_on": []
      }
    ]
  },

  "implementation_tasks": [
    {
      "service": "",
      "task": "",
      "description": ""
    }
  ],

  "development_notes": [],
  "risks": [],
  "complexity_estimate": ""
}

----------------------------------------------------
RULES
----------------------------------------------------

• Output must be valid JSON  
• Do NOT generate code  
• Do NOT include explanations outside JSON  
• Do NOT invent features not present in the PRD  
• Architecture must be detailed enough for Developer Agents to implement immediately
"""


REVISION_INSTRUCTIONS = """You previously produced a system architecture for the user. The user has reviewed it and provided revision feedback below.

Produce a REVISED architecture that:
- Addresses every point in the user's feedback explicitly.
- Preserves the parts of the previous architecture that the feedback did not contradict.
- Stays consistent with the PRD and the original user requirement.
- Is complete on its own (do not output a diff - output the full revised architecture)."""


def get_architect_prompt(user_requirements: str, prd_json, previous_architecture: dict | None = None, feedback: str = ""):
    is_revision = bool(feedback) and bool(previous_architecture)

    system_content = SYSTEM_PROMPT
    if is_revision:
        system_content = SYSTEM_PROMPT + "\n\n" + REVISION_INSTRUCTIONS

    messages = [
        SystemMessage(content=system_content),
        HumanMessagePromptTemplate.from_template("""
USER REQUIREMENTS:
{user_requirements}

PRODUCT REQUIREMENTS DOCUMENT (PRD):
{prd_json}
"""),
    ]

    fmt = {
        "user_requirements": user_requirements,
        "prd_json": prd_json,
    }

    if is_revision:
        messages.append(HumanMessagePromptTemplate.from_template("""
PREVIOUS ARCHITECTURE (JSON):
{previous_architecture}

USER REVISION FEEDBACK:
{feedback}

Produce the full revised architecture that addresses the feedback.
"""))
        fmt["previous_architecture"] = previous_architecture
        fmt["feedback"] = feedback
    else:
        messages.append(HumanMessagePromptTemplate.from_template("""
Analyze the user requirements and the PRD.

Design the complete system architecture following the required JSON format.
"""))

    prompt_template = ChatPromptTemplate.from_messages(messages)
    return prompt_template.format_messages(**fmt)
