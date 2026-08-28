"""Enums that accept how models actually write things.

Asked for ``modular_monolith``, a model will cheerfully answer ``"modular
monolith"`` or ``"Modular Monolith"`` -- the right answer in the wrong casing.
Providers that validate tool arguments against the schema reject the whole call
for it, so a single space costs an entire generation.

The value is unambiguous, so recover it rather than fail. Normalising separators
and case is enough to accept every near-miss seen in practice while still
rejecting a genuinely wrong value like ``"serverless"``.
"""

from __future__ import annotations

import re
from enum import StrEnum

_SEPARATORS = re.compile(r"[\s\-]+")


class LenientStrEnum(StrEnum):
    """A StrEnum that resolves case and separator variants of its members."""

    @classmethod
    def _missing_(cls, value: object) -> LenientStrEnum | None:
        if not isinstance(value, str):
            return None

        candidate = _SEPARATORS.sub("_", value.strip().lower())
        for member in cls:
            if member.value == candidate:
                return member

        # "eventdriven" for "event_driven": right word, no separator at all.
        squashed = candidate.replace("_", "")
        for member in cls:
            if member.value.replace("_", "") == squashed:
                return member

        return None
