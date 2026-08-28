"""Message payload helpers shared by the registry and the structured-output ladder.

Kept separate from ``llm.registry`` only so ``llm.structured`` can use it without
importing the registry that imports it.
"""

from __future__ import annotations

from typing import Any


def flatten_content(content: Any) -> str:
    """Normalise a message payload to text.

    Multimodal providers return a list of content blocks rather than a string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text", ""))
            else:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    return str(content)
