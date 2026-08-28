"""The canned fixtures must satisfy the real contracts, or every other test lies."""

from __future__ import annotations

import json

from schema.architect_schema import ArchitectSchema
from schema.developer_schema import DeveloperSchema
from schema.product_manager_schema import ManagerSchema
from schema.qa_schema import QASchema
from tests import fakes


def test_prd_matches_contract():
    prd = fakes.build_prd()
    assert isinstance(prd, ManagerSchema)
    # Round-tripping proves the dict form the agents put in state is also valid.
    assert ManagerSchema.model_validate(prd.model_dump()) == prd
    assert any(feature.is_mvp for feature in prd.features)


def test_architecture_matches_contract():
    arch = fakes.build_architecture()
    assert isinstance(arch, ArchitectSchema)
    assert ArchitectSchema.model_validate(arch.model_dump()) == arch
    assert arch.services[0].name == fakes.SERVICE_NAME


def test_developer_output_matches_contract():
    dev = fakes.build_developer_output()
    assert isinstance(dev, DeveloperSchema)
    assert DeveloperSchema.model_validate(dev.model_dump()) == dev
    assert {f.file_path for f in dev.services[0].files} == {
        "app/__init__.py",
        "app/calculator.py",
        "app/store.py",
    }


def test_qa_report_matches_contract():
    qa = fakes.build_qa_report()
    assert isinstance(qa, QASchema)
    assert QASchema.model_validate(qa.model_dump()) == qa
    assert qa.passed is True

    failed = fakes.build_qa_report(score=4, critical_issues=2)
    assert failed.passed is False
    assert failed.critical_issues == 2


def test_generated_sources_compile_or_not_as_advertised():
    compile(fakes.CALCULATOR_SOURCE, "calculator.py", "exec")
    compile(fakes.STORE_SOURCE, "store.py", "exec")
    compile(fakes.PASSING_TEST_SOURCE, "test_calculator.py", "exec")

    try:
        compile(fakes.BROKEN_SOURCE, "broken.py", "exec")
    except SyntaxError:
        return
    raise AssertionError("BROKEN_SOURCE was expected to be a syntax error")


def test_structured_llm_replays_responses_in_order():
    first, second = fakes.build_prd("One"), fakes.build_prd("Two")
    llm = fakes.FakeStructuredLLM([first, second])

    assert llm.invoke("a") is first
    assert llm.invoke("b") is second
    # Exhausted responses repeat the last one rather than raising.
    assert llm.invoke("c") is second
    assert llm.calls == 3


async def test_structured_llm_supports_async():
    prd = fakes.build_prd()
    llm = fakes.FakeStructuredLLM([prd])
    assert await llm.ainvoke("prompt") is prd


def test_text_responder_distinguishes_triage_from_prose():
    triage = fakes.fake_text_response(f"...{fakes.TRIAGE_MARKER}...")
    assert json.loads(triage) == [f"{fakes.SERVICE_SLUG}/app/calculator.py"]

    prose = fakes.fake_text_response("Write a PRD document")
    assert prose.lstrip().startswith("#")
