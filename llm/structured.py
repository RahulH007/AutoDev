"""Provider-agnostic structured output.

``with_structured_output(schema)`` commits to one mechanism -- usually tool
calling -- and a model that cannot drive that mechanism has no way to succeed.
That is not a hypothetical: a small Llama emits the JSON as literal text in a
``<function=Schema> {...}`` wrapper, and the provider rejects the request with
``tool_use_failed`` even though the payload it produced was perfectly good.

So instead of one mechanism, each model gets a ladder of them, tried in order and
degrading only as far as it has to:

1. ``native``      -- whatever the provider does by default, normally tool calling.
2. ``json_schema`` -- constrained decoding, where the provider enforces the schema.
3. ``json_mode``   -- provider guarantees valid JSON; the schema goes in the prompt.
4. ``parse``       -- plain text, then find the JSON and validate it ourselves.

The last rung asks nothing of the provider beyond returning text, so it works on
any chat model at all, including a local one with no tool support. Rungs a
provider cannot build are skipped at construction time rather than failing later.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from core.logging import get_logger
from llm.content import flatten_content
from utils.json_utils import extract_json_object

logger = get_logger(__name__)

INSTRUCTIONS = """Respond with a single JSON object and nothing else.

It must validate against this JSON Schema:

{schema}

Rules:
- Output raw JSON only. No markdown fences, no explanation, no text around it.
- Include every required property.
- Do not add properties the schema does not define.
"""


def as_messages(prompt: Any) -> list[BaseMessage]:
    """Coerce whatever an agent passed in into a message list."""
    if isinstance(prompt, str):
        return [HumanMessage(content=prompt)]
    if isinstance(prompt, BaseMessage):
        return [prompt]
    if hasattr(prompt, "to_messages"):
        return list(prompt.to_messages())
    if isinstance(prompt, Sequence):
        return list(prompt)
    return [HumanMessage(content=str(prompt))]


def schema_instructions(schema: type[BaseModel]) -> str:
    return INSTRUCTIONS.format(schema=json.dumps(schema.model_json_schema(), indent=2))


def with_schema_prompt(schema: type[BaseModel]) -> Runnable:
    """Append the schema to the prompt.

    Needed by every rung below the provider-enforced ones, where the model is
    told what shape to produce rather than being constrained to it.
    """
    instructions = schema_instructions(schema)

    def append(prompt: Any) -> list[BaseMessage]:
        return [*as_messages(prompt), HumanMessage(content=instructions)]

    return RunnableLambda(append)


def parse_into(schema: type[BaseModel]) -> Runnable:
    def parse(response: Any) -> BaseModel:
        content = flatten_content(getattr(response, "content", response))
        return schema.model_validate(extract_json_object(content))

    return RunnableLambda(parse)


StrategyBuilder = Callable[[BaseChatModel, type[BaseModel]], Runnable]

STRATEGIES: tuple[tuple[str, StrategyBuilder], ...] = (
    ("native", lambda model, schema: model.with_structured_output(schema)),
    (
        "json_schema",
        lambda model, schema: model.with_structured_output(schema, method="json_schema"),
    ),
    (
        "json_mode",
        lambda model, schema: with_schema_prompt(schema)
        | model.with_structured_output(schema, method="json_mode"),
    ),
    ("parse", lambda model, schema: with_schema_prompt(schema) | model | parse_into(schema)),
)


def announce_failure(name: str, model: BaseChatModel, runnable: Runnable) -> Runnable:
    """Log why a rung failed on its way past.

    The ladder reports only the error it ended on, so without this the rungs it
    walked past -- and why each was rejected -- leave no trace in the run log.
    """

    def report(exc: Exception) -> None:
        logger.warning(
            "Structured strategy %r failed on %s: %s",
            name,
            type(model).__name__,
            str(exc).replace("\n", " ")[:300],
        )

    def invoke(prompt: Any, config: Any = None) -> Any:
        try:
            return runnable.invoke(prompt, config)
        except Exception as exc:
            report(exc)
            raise

    async def ainvoke(prompt: Any, config: Any = None) -> Any:
        try:
            return await runnable.ainvoke(prompt, config)
        except Exception as exc:
            report(exc)
            raise

    return RunnableLambda(invoke, afunc=ainvoke, name=f"structured:{name}")


def build_strategies(model: BaseChatModel, schema: type[BaseModel]) -> list[tuple[str, Runnable]]:
    """Every rung this model can actually construct, strongest first."""
    built: list[tuple[str, Runnable]] = []

    for name, builder in STRATEGIES:
        try:
            built.append((name, announce_failure(name, model, builder(model, schema))))
        except Exception as exc:
            # Providers signal an unsupported mode by raising here, which is a
            # normal outcome rather than a problem worth surfacing.
            logger.debug(
                "Structured strategy %r unavailable on %s: %s",
                name,
                type(model).__name__,
                exc,
            )

    if not built:  # pragma: no cover - a chat model that cannot even return text
        raise RuntimeError(f"No usable structured-output strategy for {type(model).__name__}")

    return built
