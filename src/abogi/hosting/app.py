"""The Foundry hosted-agent entry point: `python -m abogi.hosting.app`.

Serves the Responses protocol on port 8088 (or ``PORT``). Each request is one
owner turn: the conversation so far and the new input become Responses input
items, the core loop runs, and its answer text is streamed back as the model writes it.

Tracing is configured by agentserver-core when
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set; tool runs and outgoing
HTTP calls (Google Calendar) get their own spans. Model-call spans also need
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

from abogi.core.approvals import Approvals, is_approval
from abogi.core.loop import Tool, ToolCall, run_tool, stream_turn
from abogi.hosting import backend
from abogi.skills import alerts as alert_skill
from abogi.skills import research as research_skill
from abogi.hosting.spoken import SpokenResponse, speakable
from abogi.hosting.instructions import INSTRUCTIONS, with_capabilities, with_model, with_now

logger = logging.getLogger(__name__)

ToolsProvider = Callable[[], Awaitable[list[Tool]]]
AlertsProvider = Callable[[datetime], Awaitable[list[dict[str, str]]]]
SkillsProvider = Callable[[Any, str], list[Tool]]


def default_skills_provider(client: Any, model: str) -> list[Tool]:
    """Skills built on the model client itself, such as research."""
    try:
        connectors = importlib.import_module("abogi.connectors").load_connectors()
    except (ImportError, AttributeError):
        connectors = []
    mail = next((c for c in connectors if hasattr(c, "save_report")), None)
    return [research_skill.ResearchSkill(client, model, mail).tool()]


async def default_alerts_provider(now: datetime) -> list[dict[str, str]]:
    """Alerts from the configured connectors (see `abogi.skills.alerts`)."""
    try:
        connectors = importlib.import_module("abogi.connectors").load_connectors()
    except (ImportError, AttributeError):
        return []
    return await alert_skill.collect(connectors, os.environ, now)

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


NOTHING_PENDING = (
    "The owner said the approval phrase, but no step is waiting for approval "
    "(it may have expired). Say so in one sentence and offer to set it up again."
)
APPROVED = "The owner approved that step and it has been run. Tell them the outcome in one sentence."
DROPPED = (
    'This turn did not approve the waiting step ("{read_back}"), so it was dropped. '
    "If the owner still seems to want it (a plain yes, say), call its tool again so "
    "it is held again, and ask for the approval phrase."
)


def latest_owner_text(items: list[dict[str, Any]]) -> str:
    for item in reversed(items):
        if item.get("role") == "user":
            return str(item.get("content") or "")
    return ""


def approval_keys(context: Any, request: Any) -> tuple[str, str | None]:
    """(where this turn holds a step, where it looks for the last turn's step).

    A conversation id names the whole conversation (Voice Live sends one).
    Otherwise turns are chained by `previous_response_id`, so a step is held
    under this response's id and found by the next turn that follows it: an
    approval counts only on the turn right after the read-back.
    """
    conversation = getattr(context, "conversation_id", None) or _field(request, "conversation")
    if isinstance(conversation, Mapping):  # {"id": ...} form
        conversation = conversation.get("id")
    if isinstance(conversation, str) and conversation:
        return f"conversation:{conversation}", f"conversation:{conversation}"
    previous = _field(request, "previous_response_id")
    return f"response:{context.response_id}", (f"response:{previous}" if previous else None)


def _field(request: Any, name: str) -> Any:
    """A request field, whether the request is a mapping or a model object."""
    if isinstance(request, Mapping):
        return request.get(name)
    return getattr(request, name, None)


async def settle_pending(
    approvals: Approvals, key: str | None, items: list[dict[str, Any]], tools: list[Tool]
) -> str | None:
    """Run or drop the conversation's pending outward step (ADR 0004).

    Called at the start of every turn. If this turn is an approval, the core
    runs the held call and adds it, with its result, to the model input, so
    the model only reports the outcome. Any other turn drops the step.

    Returns a note for this turn's instructions, or None. (Foundry's Responses
    endpoint rejects system and developer messages in the input, so a note
    cannot go there.)
    """
    pending = approvals.take(key) if key else None
    if not is_approval(latest_owner_text(items)):
        return DROPPED.format(read_back=pending.read_back) if pending else None
    if pending is None:
        return NOTHING_PENDING
    call: ToolCall = pending.call
    output = await run_tool({tool.name: tool for tool in tools}, call)
    items.append(
        {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments}
    )
    items.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
    return APPROVED


def create_app(
    *,
    client_factory: Callable[[], Any] = backend.make_client,
    model: str | None = None,
    tools_provider: ToolsProvider = default_tools_provider,
    instructions: str = INSTRUCTIONS,
    store: ResponseProviderProtocol | None = None,
    approvals: Approvals | None = None,
    alerts_provider: AlertsProvider = default_alerts_provider,
    skills_provider: SkillsProvider = default_skills_provider,
    hosted_tools: list[dict[str, Any]] | None = None,
) -> ResponsesAgentServerHost:
    """Build the server. Every argument has a production default; tests swap them."""
    app = ResponsesAgentServerHost(
        options=ResponsesServerOptions(default_fetch_history_count=20),
        store=store,
    )
    client: Any = None  # created on first request so startup needs no credentials
    approvals = approvals or Approvals()

    @app.response_handler
    async def handler(request: CreateResponse, context: ResponseContext, cancellation_signal: asyncio.Event):
        nonlocal client
        if client is None:
            client = client_factory()
        history = await context.get_history()
        current = await context.get_input_items()
        items = build_input(history, current)
        if alert_skill.is_alert_check(build_input([], current)):
            # Answered by code, no model call: the tray asks every few minutes.
            found = await alerts_provider(datetime.now(ZoneInfo(backend.owner_time_zone())))
            return TextResponse(context, request, text=alert_skill.answer(found))
        model_name = model or backend.model_name()
        tools = await tools_provider() + skills_provider(client, model_name)
        hold_key, last_key = approval_keys(context, request)
        note = await settle_pending(approvals, last_key, items, tools)
        now = datetime.now(ZoneInfo(backend.owner_time_zone()))
        turn_instructions = with_now(
            with_model(with_capabilities(instructions, [tool.name for tool in tools]), model_name),
            now,
        )
        if note:
            turn_instructions += f"\n\nAbout this turn: {note}"
        # Streamed, so Voice Live can start speaking on the first words.
        answer = stream_turn(
            client,
            model_name,
            turn_instructions,
            items,
            tools,
            reasoning_effort=backend.reasoning_effort(),
            hold=lambda call, read_back: approvals.hold(hold_key, call, read_back),
            hosted_tools=research_skill.hosted_tools() if hosted_tools is None else hosted_tools,
        )
        return SpokenResponse(context, request, text=speakable(answer))

    return app


def _instrument_http_calls() -> None:
    """Trace outgoing httpx requests, such as the Google Calendar API calls."""
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def _instrument_model_calls() -> None:
    if os.environ.get("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "").lower() != "true":
        return
    from azure.ai.projects.telemetry import AIProjectInstrumentor

    AIProjectInstrumentor().instrument()


def main() -> None:
    logging.basicConfig(level=os.environ.get("ABOGI_LOG_LEVEL", "INFO"))
    _instrument_model_calls()
    _instrument_http_calls()
    model = backend.model_name()  # fail at startup, not on the first request
    logger.info("Abogi starting: backend=%s model=%s", backend.backend_name(), model)
    create_app(model=model, tools_provider=reuse_once_loaded(default_tools_provider)).run()


if __name__ == "__main__":
    main()
