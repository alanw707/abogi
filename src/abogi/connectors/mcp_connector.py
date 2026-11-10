"""A connector reached over MCP: list the server's tools, turn each into a loop `Tool`.

Session lifetime: one MCP session per operation. `tools()` opens a session,
lists the tools and closes it; each `Tool.run` opens its own session, makes one
`tools/call` and closes it. This costs a handshake per call but needs no
lifecycle: nothing to keep alive between turns, nothing to close on shutdown,
no session bound to a task or event loop that a later turn might not share,
and a dropped connection or expired token only affects the call it happened
in. Revisit if handshake latency becomes noticeable in voice turns.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp_types import CallToolResult, EmbeddedResource, TextContent, TextResourceContents

from abogi.core.loop import Tool

logger = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]
"""Returns a bearer token; called before every HTTP request, so it may refresh."""

ToolFilter = Callable[[str], bool]
"""Decides from an MCP tool name whether the model may see that tool."""


class BearerAuth(httpx2.Auth):
    """Adds `Authorization: Bearer <token>` from an async provider to each request."""

    def __init__(self, provider: TokenProvider) -> None:
        self._provider = provider

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Bearer {await self._provider()}"
        yield request


def http_transport(url: str, token: TokenProvider | None) -> Callable[[], Any]:
    """A factory of fresh streamable-HTTP transports to `url`, one per session."""

    @asynccontextmanager
    async def connect() -> AsyncIterator[Any]:
        auth = BearerAuth(token) if token is not None else None
        async with create_mcp_http_client(auth=auth) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                yield streams

    return connect


class McpConnector:
    """Access to one MCP server, exposed to the loop as a list of tools.

    `connect` returns anything `mcp.Client` accepts as its server: a transport
    (see `http_transport`) or, in tests, an in-process `MCPServer`. It is called
    once per session, so it must return a fresh transport each time.
    """

    def __init__(
        self,
        name: str,
        connect: Callable[[], Any],
        allow: ToolFilter = lambda tool_name: True,
        url: str | None = None,
    ) -> None:
        self.name = name
        self.url = url  # for diagnostics only; never includes a token
        self._connect = connect
        self._allow = allow

    @classmethod
    def over_http(
        cls,
        name: str,
        url: str,
        token: TokenProvider | None = None,
        allow: ToolFilter = lambda tool_name: True,
    ) -> McpConnector:
        return cls(name, http_transport(url, token), allow=allow, url=url)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Client]:
        # The response cache is per-Client and every Client here is short-lived.
        async with Client(self._connect(), cache=None) as client:
            yield client

    async def tools(self) -> list[Tool]:
        """List the server's tools that pass the filter, as loop tools."""
        listed = []
        async with self._session() as client:
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor)
                listed.extend(page.tools)
                cursor = page.next_cursor
                if cursor is None:
                    break

        tools = []
        for mcp_tool in listed:
            if not self._allow(mcp_tool.name):
                logger.info("%s: hiding tool %s from the model", self.name, mcp_tool.name)
                continue
            tools.append(
                Tool(
                    name=model_safe_name(mcp_tool.name),
                    description=mcp_tool.description or mcp_tool.title or mcp_tool.name,
                    parameters=_parameters(mcp_tool.input_schema),
                    run=self._runner(mcp_tool.name),
                )
            )
        return tools

    def _runner(self, mcp_name: str) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def run(arguments: dict[str, Any]) -> str:
            try:
                async with self._session() as client:
                    result = await client.call_tool(mcp_name, arguments)
            except Exception as error:  # noqa: BLE001 - the model reads failures as text
                cause = _innermost(error)
                return (
                    f"Error from {self.name}: the call did not complete "
                    f"({type(cause).__name__}: {cause})"
                )
            if result.is_error:
                return f"Error from {self.name}: {result_text(result) or 'the tool failed.'}"
            return result_text(result)

        return run


_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def model_safe_name(name: str) -> str:
    """Function names the Responses API accepts: [A-Za-z0-9_-], at most 64 chars."""
    return _UNSAFE.sub("_", name).strip("_")[:64] or "tool"


def _innermost(error: BaseException) -> BaseException:
    """The first real error inside the exception groups anyio task groups raise."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _parameters(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = dict(schema or {})
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    return parameters


def result_text(result: CallToolResult) -> str:
    """Flatten an MCP tool result into the text the model reads."""
    parts = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, EmbeddedResource) and isinstance(
            block.resource, TextResourceContents
        ):
            parts.append(block.resource.text)
        else:
            parts.append(f"[{block.type} content omitted]")
    if not parts and result.structured_content is not None:
        return json.dumps(result.structured_content)
    return "\n".join(parts)
