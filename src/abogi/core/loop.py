"""The core agent loop: call the model, run the tools it asks for, repeat.

The loop knows nothing about Foundry. It takes any async client that speaks
the Responses API, so the same code runs against Foundry Local and a hosted
Foundry deployment.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    past_calls: list[ToolCall] = []
    step = 0
    # Sent only when set, so models that reject the parameter still work.
    options = {"reasoning": {"effort": reasoning_effort}} if reasoning_effort else {}

    while True:
        step += 1
        response = await client.responses.create(
            model=model, instructions=instructions, input=items, tools=schemas, **options
        )
        items.extend(item.model_dump(exclude_none=True) for item in response.output)
        calls = [
            ToolCall(item.call_id, item.name, item.arguments)
            for item in response.output
            if item.type == "function_call"
        ]

        action = decide(LoopState(step, max_steps, calls, past_calls))

        if action is Action.FINISH:
            return response.output_text

        if action is Action.WRAP_UP:
            # Every call must be answered before the model can be called again.
            for call in calls:
                items.append(_tool_output(call, "Not run: the step limit was reached."))
            items.append({"role": "user", "content": WRAP_UP_PROMPT})
            final = await client.responses.create(
                model=model, instructions=instructions, input=items, **options
            )
            items.extend(item.model_dump(exclude_none=True) for item in final.output)
            return final.output_text

        for call in calls:
            items.append(_tool_output(call, await _run_tool(by_name, call)))
        past_calls.extend(calls)


async def _run_tool(by_name: dict[str, Tool], call: ToolCall) -> str:
    """Run one tool. Failures go back to the model as text so it can recover."""
    tool = by_name.get(call.name)
    if tool is None:
        return f"Error: there is no tool named {call.name}."
    try:
        return await tool.run(json.loads(call.arguments or "{}"))
    except Exception as error:  # noqa: BLE001 - the model needs to see any failure
        return f"Error: {type(error).__name__}: {error}"


def _tool_output(call: ToolCall, output: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call.call_id, "output": output}
