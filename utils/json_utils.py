from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)

_FENCED = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def save_json(data: Any, path: Path) -> Path:
    """Write pretty-printed JSON, creating parent directories as needed.

    ``default=str`` keeps serialisation from failing on values a schema may carry
    that JSON does not know about, such as ``Path`` or ``datetime``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Saved JSON artifact: %s", path.name)
    return path


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_json_object(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of a plain-text model response.

    Weaker models wrap the object in markdown fences, prepend a sentence, or emit
    a textual tool call like ``<function=Schema> {...}`` instead of a real one.
    Locating the outermost braces recovers the payload from all three, which is
    what lets a model with no working tool-calling support still be usable.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty response")

    fenced = _FENCED.findall(text)
    if fenced:
        text = fenced[0].strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in response: {text[:120]!r}")

    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Parsed value is not an object")
    return parsed


def extract_json_list(raw: str) -> list[Any]:
    """Pull a JSON array out of a plain-text model response.

    Models wrap arrays in markdown fences or add a sentence of explanation despite
    instructions not to, so locate the array rather than trusting the whole string.
    Raises ValueError if there is nothing parseable.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty response")

    fenced = _FENCED.findall(text)
    if fenced:
        text = fenced[0].strip()

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON array found in response: {text[:120]!r}")

    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("Parsed value is not a list")
    return parsed
