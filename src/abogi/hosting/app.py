"""The Foundry hosted-agent entry point: `python -m abogi.hosting.app`.

Serves the Responses protocol on port 8088 (or ``PORT``). Each request is one
owner turn: the conversation so far and the new input become Responses input
items, the core loop runs to completion, and its text answer is returned.

Tracing is configured by agentserver-core when
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set. Model-call spans also need
``AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponseProviderProtocol,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)

from abogi.core.loop import Tool, run_turn
from abogi.hosting import backend
from abogi.hosting.instructions import INSTRUCTIONS, with_capabilities, with_now

logger = logging.getLogger(__name__)

ToolsProvider = Callable[[], Awaitable[list[Tool]]]

_TEXT_PARTS = {"input_text", "output_text"}
_ROLES = {"user", "assistant"}


def build_input(history: Iterable[Mapping[str, Any]], current: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn stored history plus this request's input into loop input items.

    Only text messages carry over. The loop's own tool calls from earlier turns
    are not stored by the platform (only the final answer is), so there is
    nothing else to replay.
    """
    items: list[dict[str, Any]] = []
    for item in [*history, *current]:
        if item.get("type", "message") != "message" or item.get("role") not in _ROLES:
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(
                part.get("text") or ""
                for part in content or []
                if part.get("type") in _TEXT_PARTS and part.get("text")
            )
        if text:
            items.append({"role": item["role"], "content": text})
    return items


async def default_tools_provider() -> list[Tool]:
    """Tools from `abogi.connectors.load_tools()` if it exists, else none."""
    try:
        connectors = importlib.import_module("abogi.connectors")
    except ImportError:
        return []
    load_tools = getattr(connectors, "load_tools", None)
    if load_tools is None:
        return []
    tools = load_tools()
    if inspect.isawaitable(tools):
        tools = await tools
    return list(tools)


def reuse_once_loaded(provider: ToolsProvider) -> ToolsProvider:
    """Load tools until some arrive, then reuse them for every later turn.

    Connector tools hold no open connection, so reuse is safe; retrying while
    the list is empty lets a connector that was down at startup appear later.
    """
    loaded: list[Tool] = []

    async def tools() -> list[Tool]:
        if not loaded:
            loaded.extend(await provider())
        return list(loaded)

    return tools


def create_app(
    *,
    client_factory: Callable[[], Any] = backend.make_client,
    model: str | None = None,
    tools_provider: ToolsProvider = default_tools_provider,
    instructions: str = INSTRUCTIONS,
    store: ResponseProviderProtocol | None = None,
) -> ResponsesAgentServerHost:
    """Build the server. Every argument has a production default; tests swap them."""
    app = ResponsesAgentServerHost(
        options=ResponsesServerOptions(default_fetch_history_count=20),
        store=store,
    )
    client: Any = None  # created on first request so startup needs no credentials

    @app.response_handler
    async def handler(request: CreateResponse, context: ResponseContext, cancellation_signal: asyncio.Event):
        nonlocal client
        if client is None:
            client = client_factory()
        history = await context.get_history()
        current = await context.get_input_items()
        items = build_input(history, current)
        tools = await tools_provider()
        now = datetime.now(ZoneInfo(backend.owner_time_zone()))
        turn_instructions = with_now(
            with_capabilities(instructions, [tool.name for tool in tools]), now
        )
        answer = await run_turn(
            client,
            model or backend.model_name(),
            turn_instructions,
            items,
            tools,
            reasoning_effort=backend.reasoning_effort(),
        )
        return TextResponse(context, request, text=answer)

    return app


def _instrument_model_calls() -> None:
    if os.environ.get("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "").lower() != "true":
        return
    from azure.ai.projects.telemetry import AIProjectInstrumentor

    AIProjectInstrumentor().instrument()


def main() -> None:
    logging.basicConfig(level=os.environ.get("ABOGI_LOG_LEVEL", "INFO"))
    _instrument_model_calls()
    model = backend.model_name()  # fail at startup, not on the first request
    logger.info("Abogi starting: backend=%s model=%s", backend.backend_name(), model)
    create_app(model=model, tools_provider=reuse_once_loaded(default_tools_provider)).run()


if __name__ == "__main__":
    main()
