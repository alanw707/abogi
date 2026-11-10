"""Connectors, driven against an in-process MCP server so no network is needed."""

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from abogi.connectors import load_tools
from abogi.connectors.calendar import (
    build_calendar_connector,
    is_read_only,
    toolbox_url,
)
from abogi.connectors.mcp_connector import McpConnector, result_text
from abogi.core.loop import Tool


def calendar_server() -> MCPServer:
    """A stand-in calendar MCP server with both read and write tools."""
    server = MCPServer("fake-calendar")

    @server.tool(description="List events between two times.")
    def list_events(calendar_id: str, time_min: str = "") -> str:
        return f"events in {calendar_id} from {time_min or 'now'}"

    @server.tool(name="get-freebusy", description="Busy blocks.")
    def freebusy(calendar_id: str) -> str:
        return f"{calendar_id} is free"

    @server.tool(description="Create an event.")
    def create_event(summary: str) -> str:
        return "created"

    @server.tool(description="Delete an event.")
    def delete_event(event_id: str) -> str:
        return "deleted"

    @server.tool(description="Always fails.")
    def get_broken(event_id: str) -> str:
        raise ToolError("calendar backend unavailable")

    return server


def connector(server: MCPServer, allow=is_read_only) -> McpConnector:
    return McpConnector("calendar", connect=lambda: server, allow=allow)


# --- tool conversion -------------------------------------------------------


async def test_mcp_tools_become_loop_tools():
    tools = await connector(calendar_server(), allow=lambda name: True).tools()

    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "list_events",
        "get-freebusy",
        "create_event",
        "delete_event",
        "get_broken",
    }
    listing = by_name["list_events"]
    assert isinstance(listing, Tool)
    assert listing.description == "List events between two times."
    assert listing.parameters["type"] == "object"
    assert set(listing.parameters["properties"]) == {"calendar_id", "time_min"}
    assert listing.parameters["required"] == ["calendar_id"]


async def test_running_a_tool_calls_the_mcp_server_and_returns_text():
    tools = {t.name: t for t in await connector(calendar_server()).tools()}

    output = await tools["list_events"].run({"calendar_id": "primary", "time_min": "9am"})

    assert output == "events in primary from 9am"


async def test_tool_names_are_made_safe_for_the_model_but_called_by_original_name():
    server = MCPServer("odd-names")

    @server.tool(name="calendar.list events")
    def odd() -> str:
        return "ok"

    [tool] = await McpConnector("x", connect=lambda: server).tools()

    assert tool.name == "calendar_list_events"
    assert await tool.run({}) == "ok"


def test_result_text_joins_text_and_marks_other_content():
    from mcp_types import CallToolResult, ImageContent, TextContent

    result = CallToolResult(
        content=[
            TextContent(text="first"),
            ImageContent(data="AAAA", mime_type="image/png"),
            TextContent(text="second"),
        ]
    )

    assert result_text(result) == "first\n[image content omitted]\nsecond"


def test_result_text_falls_back_to_structured_content():
    from mcp_types import CallToolResult

    result = CallToolResult(content=[], structured_content={"busy": []})

    assert result_text(result) == '{"busy": []}'


# --- the read-only filter --------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        # nspady/google-calendar-mcp style
        "list-calendars",
        "list-events",
        "search-events",
        "get-event",
        "get-freebusy",
        "get-current-time",
        "list-colors",
        # snake_case / Google remote server style
        "list_calendars",
        "list_events",
        "get_event",
        "search_events",
        "query_freebusy",
        "freebusy",
        "suggest_time",
        # camelCase and prefixed names
        "listEvents",
        "calendar.get_event",
    ],
)
def test_read_tools_are_allowed(name):
    assert is_read_only(name)


@pytest.mark.parametrize(
    "name",
    [
        "create-event",
        "update-event",
        "delete-event",
        "respond-to-event",
        "create_event",
        "update_event",
        "delete_event",
        "respond_to_event",
        "quick_add_event",
        "move_event",
        "patch_event",
        "insert_event",
        "createEvent",
        # a read verb does not excuse a write verb
        "get_or_create_calendar",
        "list_and_delete_events",
        # unknown verbs are excluded: the filter is an allow-list
        "manage_calendar",
        "events",
        "",
    ],
)
def test_write_and_unknown_tools_are_excluded(name):
    assert not is_read_only(name)


async def test_model_never_sees_calendar_write_tools():
    names = {tool.name for tool in await connector(calendar_server()).tools()}

    assert names == {"list_events", "get-freebusy", "get_broken"}


# --- failures --------------------------------------------------------------


async def test_a_failing_tool_returns_error_text_instead_of_raising():
    tools = {t.name: t for t in await connector(calendar_server()).tools()}

    output = await tools["get_broken"].run({"event_id": "e1"})

    assert output.startswith("Error from calendar:")
    assert "calendar backend unavailable" in output


async def test_a_session_failure_during_a_call_returns_error_text():
    server = calendar_server()
    attempts = []

    def connect():
        attempts.append(1)
        if len(attempts) > 1:  # listing works, the later call cannot connect
            raise ConnectionError("connection refused")
        return server

    [listing, *_] = await McpConnector("calendar", connect=connect, allow=is_read_only).tools()

    output = await listing.run({"calendar_id": "primary"})

    assert output == (
        "Error from calendar: the call did not complete (ConnectionError: connection refused)"
    )


async def test_unreachable_server_is_skipped_by_load_tools(monkeypatch):
    monkeypatch.setenv("ABOGI_CALENDAR_MCP_URL", "http://127.0.0.1:9/mcp")
    monkeypatch.setenv("ABOGI_CALENDAR_MCP_TOKEN", "dev-token")
    monkeypatch.delenv("ABOGI_CALENDAR_TOOLBOX", raising=False)

    assert await load_tools() == []


# --- configuration ---------------------------------------------------------


@pytest.fixture
def no_calendar_env(monkeypatch):
    for name in (
        "ABOGI_CALENDAR_TOOLBOX",
        "ABOGI_CALENDAR_MCP_URL",
        "ABOGI_CALENDAR_MCP_TOKEN",
        "FOUNDRY_PROJECT_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


async def test_no_configuration_means_no_tools(no_calendar_env):
    assert build_calendar_connector({}) is None
    assert await load_tools() == []


def test_toolbox_url_is_built_from_the_project_endpoint():
    url = toolbox_url("https://acct.services.ai.azure.com/api/projects/p1/", "calendar")

    assert url == (
        "https://acct.services.ai.azure.com/api/projects/p1"
        "/toolboxes/calendar/mcp?api-version=v1"
    )


def test_toolbox_configuration_builds_a_connector():
    env = {
        "ABOGI_CALENDAR_TOOLBOX": "calendar",
        "FOUNDRY_PROJECT_ENDPOINT": "https://acct.services.ai.azure.com/api/projects/p1",
    }

    built = build_calendar_connector(env)

    assert built is not None
    assert built.url.endswith("/toolboxes/calendar/mcp?api-version=v1")


def test_toolbox_without_project_endpoint_is_a_configuration_error():
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        build_calendar_connector({"ABOGI_CALENDAR_TOOLBOX": "calendar"})


def test_direct_url_configuration_builds_a_connector():
    env = {"ABOGI_CALENDAR_MCP_URL": "http://localhost:3000/mcp", "ABOGI_CALENDAR_MCP_TOKEN": "t"}

    built = build_calendar_connector(env)

    assert built is not None
    assert built.url == "http://localhost:3000/mcp"


def test_both_sources_configured_is_a_configuration_error():
    env = {
        "ABOGI_CALENDAR_TOOLBOX": "calendar",
        "FOUNDRY_PROJECT_ENDPOINT": "https://example/api/projects/p1",
        "ABOGI_CALENDAR_MCP_URL": "http://localhost:3000/mcp",
    }

    with pytest.raises(ValueError, match="not both"):
        build_calendar_connector(env)


async def test_bearer_token_is_sent_on_each_request():
    import httpx2

    from abogi.connectors.mcp_connector import BearerAuth

    calls = []

    async def provider():
        calls.append(1)
        return f"token-{len(calls)}"

    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx2.Response(200)

    async with httpx2.AsyncClient(
        auth=BearerAuth(provider), transport=httpx2.MockTransport(handler)
    ) as client:
        await client.get("http://example.test/")
        await client.get("http://example.test/")

    assert seen == ["Bearer token-1", "Bearer token-2"]
