from __future__ import annotations

from typing import Any

from core import manifest as manifest_util
from utils.json_utils import compact_json


def get_qa_triage_prompt(
    prd_json: dict[str, Any],
    architect_json: dict[str, Any],
    code_manifest: dict[str, Any],
) -> str:
    """Cheap first pass: decide which files are worth reading in full.

    Only the manifest is sent, never file contents, which keeps this call small
    even for a large generated project.
    """
    known_paths = manifest_util.qualified_paths(code_manifest)

    return f"""You are a QA engineer triaging a code review. Identify which files are most likely to contain
bugs, security issues, or logic errors and therefore need a detailed read.

PRODUCT REQUIREMENTS (abridged):
{compact_json({k: prd_json.get(k) for k in ("product_name", "features", "functional_requirements")})}

ARCHITECTURE (abridged):
{compact_json({k: architect_json.get(k) for k in ("architecture_style", "services")})}

FILE MANIFEST:
{manifest_util.summarise(code_manifest)}

Prioritise:
- Authentication and authorisation logic
- Database models and migrations
- API route handlers and business logic
- Anything handling user input, money, or permissions
- Configuration and environment handling

Deprioritise: static assets, styling, boilerplate entry points, and generated index files.

Choose only from these exact paths:
{compact_json(known_paths)}

Return ONLY a valid JSON array of the paths you selected, copied exactly as written above.
No markdown, no explanation, no trailing commentary.
Example: ["backend-api/app/api/auth.py", "backend-api/app/models/user.py"]
"""
