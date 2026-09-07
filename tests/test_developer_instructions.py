"""The developer instructions, compacted without losing a requirement.

The block was 1,371 tokens, of which 212 were separator rules made of hyphens.
Several requirements were also stated twice, and one — "output valid JSON, no
markdown fences" — is already enforced by the structured-output ladder and
repeated verbatim in `llm/structured.py:INSTRUCTIONS`.

Compaction is only safe if every distinct requirement survives, so this file
lists them. A requirement removed on purpose should fail here first and be
deleted from the list deliberately, rather than vanishing unnoticed.
"""

from __future__ import annotations

import re

from prompts.developer_json_prompt import BASE_INSTRUCTIONS, FIX_INSTRUCTIONS

# One entry per distinct requirement, as a case-insensitive substring or regex
# that must still appear. Grouped the way the instructions are.
REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "completeness": (
        r"no TODOs?|no stubs?|placeholder",
        r"project_structure",
        r"every feature|all features|features from the PRD",
    ),
    "paths": (
        r"relative",
        r"app/main\.py",
        r"drive letter|\.\.",
        r"service_name",
    ),
    "backend": (
        r"router",
        r"[Pp]ydantic",
        r"JWT",
        r"register",
        r"login",
        r"middleware",
        r"bcrypt",
        r"SQLAlchemy",
        r"ForeignKey|relationship",
        r"authenticated user",
        r"CORS",
        r'detail',
        r"DATABASE_URL",
        r"SQLite",
        r"os\.getenv|pydantic-settings",
        r"\.env\.example",
    ),
    "frontend": (
        r"React Router",
        r"[Pp]rotected route",
        r"/login",
        r"localStorage",
        r"Bearer",
        r"[Aa]xios",
        r"baseURL",
        r"REACT_APP_API_URL",
        r"reusable component",
        r"loading",
        r"error state",
        r"Context|auth hook",
        r"Tailwind|CSS framework",
    ),
    "testability": (
        r"importable",
        r"import time|import-time",
        r"module-level `?app`?",
    ),
    "dependencies": (
        r"requirements\.txt",
        r"pinned|exact version",
        r"package\.json",
        r"scripts",
    ),
    "readme": (
        r"readme_content",
        r"API|endpoint",
        r"troubleshoot",
    ),
    "absolute": (
        r"[Dd]ockerfile|docker-compose",
        r"hardcoded|hard-coded",
    ),
}


class TestEveryRequirementSurvives:
    def test_each_requirement_is_still_stated(self):
        missing = [
            (group, pattern)
            for group, patterns in REQUIREMENTS.items()
            for pattern in patterns
            if not re.search(pattern, BASE_INSTRUCTIONS, re.IGNORECASE)
        ]
        assert not missing, f"compaction dropped: {missing}"

    def test_the_fix_instructions_keep_their_rules(self):
        for pattern in (
            r"only the files that need to change|only the files that changed",
            r"suggested_fix",
            r"not break a passing test|do not break",
            r"priority|first",
        ):
            assert re.search(pattern, FIX_INSTRUCTIONS, re.IGNORECASE), pattern


class TestTheBlockIsActuallySmaller:
    def test_it_fits_the_budget_it_was_compacted_for(self):
        """1,371 tokens before. The developer prompt had 639 to find."""
        assert len(BASE_INSTRUCTIONS) // 4 < 1000

    def test_the_hyphen_rules_are_gone(self):
        """212 tokens of separator decoration carried no instruction."""
        assert "-" * 20 not in BASE_INSTRUCTIONS


class TestDuplicationIsGone:
    """Each of these was stated in two places; one statement is enough."""

    def test_env_example_is_asked_for_once(self):
        assert BASE_INSTRUCTIONS.lower().count(".env.example") == 1

    def test_stubs_are_forbidden_once(self):
        assert BASE_INSTRUCTIONS.lower().count("stub") == 1

    def test_json_only_is_left_to_the_structured_ladder(self):
        """llm/structured.py already says this on the rungs that need it, and the
        provider enforces it on the rungs that do not."""
        assert "markdown fence" not in BASE_INSTRUCTIONS.lower()
