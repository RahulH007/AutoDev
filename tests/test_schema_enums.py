"""Enum fields must survive how models actually spell things.

The motivating failure: a model answered ``"modular monolith"`` for a field whose
only legal value was ``"modular_monolith"``, and the whole generation was thrown
away over one space.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from schema.architect_schema import ArchitectureStyle
from schema.enums import LenientStrEnum
from schema.product_manager_schema import ComplexityLevel, Priority
from schema.qa_schema import Severity


class TestSeparatorsAndCase:
    @pytest.mark.parametrize(
        "written",
        [
            "modular_monolith",
            "modular monolith",
            "Modular Monolith",
            "MODULAR_MONOLITH",
            "modular-monolith",
            "  modular monolith  ",
            "modularmonolith",
        ],
    )
    def test_every_spelling_of_modular_monolith_resolves(self, written: str):
        assert ArchitectureStyle(written) is ArchitectureStyle.MODULAR_MONOLITH

    def test_it_applies_to_the_other_schemas_enums(self):
        assert Priority("High") is Priority.HIGH
        assert ComplexityLevel(" LOW ") is ComplexityLevel.LOW
        assert Severity("Critical") is Severity.CRITICAL

    def test_the_canonical_value_is_unchanged(self):
        assert ArchitectureStyle.EVENT_DRIVEN.value == "event_driven"


class TestItStillRejectsWrongValues:
    """Leniency about spelling must not become leniency about meaning."""

    @pytest.mark.parametrize("written", ["serverless", "", "micro services!", "monolithic"])
    def test_a_value_that_is_not_a_member_still_fails(self, written: str):
        with pytest.raises(ValueError):
            ArchitectureStyle(written)

    def test_non_strings_are_not_coerced(self):
        with pytest.raises(ValueError):
            ArchitectureStyle(3)


class TestThroughPydantic:
    def test_a_model_accepts_the_loose_spelling(self):
        class Design(BaseModel):
            style: ArchitectureStyle

        assert Design(style="Event Driven").style is ArchitectureStyle.EVENT_DRIVEN

    def test_a_model_still_rejects_an_unknown_style(self):
        class Design(BaseModel):
            style: ArchitectureStyle

        with pytest.raises(ValidationError):
            Design(style="serverless")


def test_the_base_class_works_for_any_enum():
    class Colour(LenientStrEnum):
        DEEP_BLUE = "deep_blue"

    assert Colour("Deep Blue") is Colour.DEEP_BLUE
