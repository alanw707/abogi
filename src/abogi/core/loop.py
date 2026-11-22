"""The core agent loop: call the model, run the tools it asks for, repeat.

The loop knows nothing about Foundry. It takes any async client that speaks
the Responses API, so the same code runs against Foundry Local and a hosted
Foundry deployment.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import StatusCode

_tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class Tool:
    """One function the model may call. Connectors supply these."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments
    run: Callable[[dict[str, Any]], Awaitable[str]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text, exactly as the model produced it


class Action(Enum):
    FINISH = "finish"  # the model's text is the answer; return it
    RUN_TOOLS = "run_tools"  # run the requested tools and call the model again
    WRAP_UP = "wrap_up"  # stop using tools; ask the model to say where it got to


@dataclass
class LoopState:
    """What the stop policy can see when it decides."""

    step: int  # 1 for the first model call of this turn
    max_steps: int
    calls: list[ToolCall]  # tool calls in the latest model response
    past_calls: list[ToolCall] = field(default_factory=list)  # from earlier steps


def decide(state: LoopState) -> Action:
    """The stop policy: what happens after each model response."""
    if not state.calls:
        return Action.FINISH
    if state.step >= state.max_steps:
        return Action.WRAP_UP
    # A model repeating an identical call it already made is stuck, not retrying:
    # a real retry after a tool error changes the arguments.
    seen = {(c.name, c.arguments) for c in state.past_calls}
    if all((c.name, c.arguments) in seen for c in state.calls) and state.step > 2:
        return Action.WRAP_UP
    return Action.RUN_TOOLS


WRAP_UP_PROMPT = (
    "Stop using tools. Tell the owner briefly what you found, what you did, "
    "and what is still unfinished."
)


async def run_turn(
    client: Any,
    model: str,
    instructions: str,
    items: list[dict[str, Any]],
    tools: list[Tool],
    max_steps: int = 12,
    reasoning_effort: str | None = None,
) -> str:
    """Run one owner turn to completion and return the spoken-length answer.

    `items` is the conversation so far in Responses API input format. It is
    extended in place with everything the model and the tools produce.
    """
    chunks = [
        chunk
        async for chunk in stream_turn(
            client, model, instructions, items, tools, max_steps, reasoning_effort
        )
    ]
    return "".join(chunks)


async def stream_turn(
    client: Any,
    model: str,
    instructions: str,
    items: list[dict[str, Any]],
    tools: list[Tool],
    max_steps: int = 12,
    reasoning_effort: str | None = None,
) -> AsyncIterator[str]:
    """Like `run_turn`, but yields the answer text as the model writes it.

    A voice channel can start speaking on the first words instead of waiting
    for the whole answer. Text the model writes alongside a tool call (such as
    "Let me check.") is yielded too, so the owner hears it before the tool runs.
    """
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    past_calls: list[ToolCall] = []
    step = 0
    ends_with_space = True  # nothing said yet, so no separator is needed
    # Sent only when set, so models that reject the parameter still work.
    options = {"reasoning": {"effort": reasoning_effort}} if reasoning_effort else {}

    async def call_model(**request: Any) -> AsyncIterator[str | Any]:
        """Yield text deltas, then the finished response as the last item."""
        nonlocal ends_with_space
        first = True
        stream = _model_stream(
            client, model=model, instructions=instructions, input=items, **options, **request
        )
        async for part in stream:
            if isinstance(part, str):
                if first and not ends_with_space and not part[:1].isspace():
                    part = " " + part  # keep this step's words apart from the last step's
                first = False
                ends_with_space = part[-1:].isspace()
            yield part

    while True:
        step += 1
        response: Any = None
        async for part in call_model(tools=schemas):
            if isinstance(part, str):
                yield part
            else:
                response = part
        items.extend(item.model_dump(exclude_none=True) for item in response.output)
        calls = [
            ToolCall(item.call_id, item.name, item.arguments)
            for item in response.output
            if item.type == "function_call"
        ]

        action = decide(LoopState(step, max_steps, calls, past_calls))

        if action is Action.FINISH:
            return

        if action is Action.WRAP_UP:
            # Every call must be answered before the model can be called again.
            for call in calls:
                items.append(_tool_output(call, "Not run: the step limit was reached."))
            items.append({"role": "user", "content": WRAP_UP_PROMPT})
            async for part in call_model():
                if isinstance(part, str):
                    yield part
                else:
                    items.extend(item.model_dump(exclude_none=True) for item in part.output)
            return

        if not ends_with_space:
            # Close the lead-in ("Let me check.") now: a speech engine waits for
            # what follows a full stop before it speaks the sentence, and without
            # this the next character would only come after the tools have run.
            ends_with_space = True
            yield " "
        for call in calls:
            items.append(_tool_output(call, await _run_tool(by_name, call)))
        past_calls.extend(calls)


async def _model_stream(client: Any, **request: Any) -> AsyncIterator[str | Any]:
    """Call the model with streaming; yield text deltas, then the final response.

    A client that ignores `stream` and returns a whole response (some local
    servers, and the test fakes) still works: its text comes as one delta.
    """
    result = await client.responses.create(stream=True, **request)
    if not hasattr(result, "__aiter__"):
        if result.output_text:
            yield result.output_text
        yield result
        return
    final = None
    async for event in result:
        kind = getattr(event, "type", "")
        if kind == "response.output_text.delta" and event.delta:
            yield event.delta
        elif kind in ("response.completed", "response.incomplete"):
            final = event.response  # incomplete (say, out of tokens) still has usable output
        elif kind in ("response.failed", "error"):
            response = getattr(event, "response", None)
            detail = getattr(response, "error", None) or getattr(event, "message", None)
            raise RuntimeError(f"Model call ended with {kind}: {detail}")
    if final is None:
        raise RuntimeError("Model stream ended without a finished response")
    yield final


async def _run_tool(by_name: dict[str, Tool], call: ToolCall) -> str:
    """Run one tool. Failures go back to the model as text so it can recover.

    Each run is an `execute_tool` span (OpenTelemetry GenAI conventions), so a
    slow turn shows whether the time went to a tool or to the model. Without a
    tracer provider configured the span is a no-op.
    """
    with _tracer.start_as_current_span(
        f"execute_tool {call.name}",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": call.name,
            "gen_ai.tool.call.id": call.call_id,
        },
    ) as span:
        tool = by_name.get(call.name)
        if tool is None:
            span.set_status(StatusCode.ERROR, "unknown tool")
            return f"Error: there is no tool named {call.name}."
        try:
            return await tool.run(json.loads(call.arguments or "{}"))
        except Exception as error:  # noqa: BLE001 - the model needs to see any failure
            span.record_exception(error)
            span.set_status(StatusCode.ERROR, type(error).__name__)
            return f"Error: {type(error).__name__}: {error}"


def _tool_output(call: ToolCall, output: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call.call_id, "output": output}
