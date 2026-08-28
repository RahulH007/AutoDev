"""The structured-output ladder.

The point of these is that swapping the configured model must never silently
cost the ability to produce structured output, so each test stands up a model
with a different set of broken capabilities and asserts a valid object still
comes out the other side.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from llm import registry
from llm.structured import as_messages, build_strategies, parse_into, with_schema_prompt
from utils.json_utils import extract_json_object


class Widget(BaseModel):
    name: str
    size: int


VALID = {"name": "sprocket", "size": 3}


class FakeModel(Runnable):
    """A chat model with configurable capabilities.

    ``supports`` lists the ``with_structured_output`` methods it can build;
    anything else raises at construction time the way a real provider does.
    ``failing`` lists methods that build fine but blow up when invoked, which is
    the Groq ``tool_use_failed`` shape. ``error`` overrides what they raise, so a
    test can distinguish a capability failure from a transient one.
    """

    def __init__(
        self,
        *,
        supports: tuple[str, ...] = ("default", "json_schema", "json_mode"),
        failing: tuple[str, ...] = (),
        error: Exception | None = None,
        text: str = json.dumps(VALID),
    ) -> None:
        self.supports = supports
        self.failing = failing
        self.error = error
        self.text = text
        self.calls: list[str] = []
        self.prompts: list[Any] = []

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls.append("text")
        self.prompts.append(input)
        return AIMessage(content=self.text)

    def with_structured_output(
        self, schema: type[BaseModel], method: str | None = None, **kwargs: Any
    ) -> Runnable:
        key = method or "default"
        if key not in self.supports:
            raise NotImplementedError(f"{key} is not supported by this model")

        def run(prompt: Any) -> BaseModel:
            self.calls.append(key)
            self.prompts.append(prompt)
            if key in self.failing:
                raise self.error or RuntimeError(f"tool_use_failed via {key}")
            return schema(**VALID)

        return RunnableLambda(run)


# ── JSON recovery ────────────────────────────────────────────────


class TestExtractJsonObject:
    def test_plain_object(self):
        assert extract_json_object('{"name": "a", "size": 1}') == {"name": "a", "size": 1}

    def test_markdown_fenced(self):
        raw = 'Here you go:\n```json\n{"name": "a", "size": 1}\n```\n'
        assert extract_json_object(raw) == {"name": "a", "size": 1}

    def test_textual_tool_call_wrapper(self):
        """The exact shape a small Llama emits when it cannot really call a tool."""
        raw = '<function=Widget> {"name": "a", "size": 1}'
        assert extract_json_object(raw) == {"name": "a", "size": 1}

    def test_surrounding_prose(self):
        raw = 'Sure! {"name": "a", "size": 1} Let me know if you need changes.'
        assert extract_json_object(raw) == {"name": "a", "size": 1}

    def test_empty_response_is_rejected(self):
        with pytest.raises(ValueError, match="Empty response"):
            extract_json_object("   ")

    def test_response_without_an_object_is_rejected(self):
        with pytest.raises(ValueError, match="No JSON object"):
            extract_json_object("I cannot help with that.")

    def test_array_is_rejected(self):
        with pytest.raises(ValueError):
            extract_json_object("[1, 2, 3]")


# ── Prompt handling ──────────────────────────────────────────────


class TestPromptCoercion:
    def test_string_becomes_a_human_message(self):
        assert as_messages("hello") == [HumanMessage(content="hello")]

    def test_message_list_is_preserved(self):
        messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
        assert as_messages(messages) == messages

    def test_single_message_is_wrapped(self):
        message = HumanMessage(content="hi")
        assert as_messages(message) == [message]

    def test_schema_is_appended_without_losing_the_original(self):
        original = [SystemMessage(content="sys"), HumanMessage(content="build a widget")]
        result = with_schema_prompt(Widget).invoke(original)

        assert result[:2] == original
        appended = result[-1].content
        assert "size" in appended and "name" in appended
        assert "JSON Schema" in appended


class TestParseInto:
    def test_parses_a_message(self):
        widget = parse_into(Widget).invoke(AIMessage(content=json.dumps(VALID)))
        assert widget == Widget(**VALID)

    def test_parses_multimodal_content_blocks(self):
        blocks = [{"type": "text", "text": json.dumps(VALID)}]
        assert parse_into(Widget).invoke(AIMessage(content=blocks)) == Widget(**VALID)


# ── The ladder ───────────────────────────────────────────────────


class TestBuildStrategies:
    def test_a_capable_model_offers_every_rung(self):
        names = [name for name, _ in build_strategies(FakeModel(), Widget)]
        assert names == ["native", "json_schema", "json_mode", "parse"]

    def test_unsupported_rungs_are_skipped(self):
        model = FakeModel(supports=("default",))
        names = [name for name, _ in build_strategies(model, Widget)]
        assert names == ["native", "parse"]

    def test_a_model_with_no_structured_support_still_gets_the_parse_floor(self):
        model = FakeModel(supports=())
        names = [name for name, _ in build_strategies(model, Widget)]
        assert names == ["parse"]

    def test_the_parse_floor_works_on_a_text_only_model(self):
        model = FakeModel(supports=(), text=f'```json\n{json.dumps(VALID)}\n```')
        _, runnable = build_strategies(model, Widget)[0]
        assert runnable.invoke("build a widget") == Widget(**VALID)


# ── Integration through the registry ─────────────────────────────


@pytest.fixture
def only_model(monkeypatch: pytest.MonkeyPatch):
    """Point the registry at a single fake model regardless of configuration."""

    def install(model: FakeModel) -> FakeModel:
        monkeypatch.setattr(registry, "get_chat_model", lambda *a, **k: model)
        return model

    return install


class TestRegistryFallback:
    def test_native_is_used_when_it_works(self, only_model):
        model = only_model(FakeModel())
        assert registry.get_structured_llm(Widget).invoke("go") == Widget(**VALID)
        assert model.calls == ["default"]

    def test_falls_past_a_broken_tool_call_to_the_next_rung(self, only_model):
        model = only_model(FakeModel(failing=("default",)))

        assert registry.get_structured_llm(Widget).invoke("go") == Widget(**VALID)
        assert model.calls[-1] == "json_schema"
        assert set(model.calls) == {"default", "json_schema"}

    def test_falls_all_the_way_to_parsing_raw_text(self, only_model):
        model = only_model(
            FakeModel(
                supports=("default", "json_mode"),
                failing=("default", "json_mode"),
                text=f'<function=Widget> {json.dumps(VALID)}',
            )
        )

        assert registry.get_structured_llm(Widget).invoke("go") == Widget(**VALID)
        assert model.calls[-1] == "text"

    def test_a_text_only_model_is_still_usable(self, only_model):
        model = only_model(FakeModel(supports=()))
        assert registry.get_structured_llm(Widget).invoke("go") == Widget(**VALID)
        assert model.calls == ["text"]

    def test_a_rung_the_model_cannot_drive_is_attempted_once(self, only_model):
        """Nothing about an identical second call would change a tool_use_failed."""
        model = only_model(FakeModel(failing=("default",)))
        registry.get_structured_llm(Widget).invoke("go")

        assert model.calls.count("default") == 1

    def test_a_rung_that_failed_transiently_is_retried_before_degrading(self, only_model):
        """Degrading on a dropped connection would give up the best rung for nothing."""
        model = only_model(
            FakeModel(failing=("default",), error=ConnectionError("connection reset by peer"))
        )
        registry.get_structured_llm(Widget).invoke("go")

        assert model.calls.count("default") == registry.get_settings().llm_max_retries
        assert model.calls[-1] == "json_schema"

    def test_a_lower_rung_is_not_retried_on_a_capability_failure(self, only_model):
        model = only_model(
            FakeModel(supports=("default", "json_mode"), failing=("default", "json_mode"))
        )
        registry.get_structured_llm(Widget).invoke("go")

        assert model.calls.count("json_mode") == 1
