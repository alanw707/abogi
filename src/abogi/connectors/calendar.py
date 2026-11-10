"""The Google Calendar connector: an MCP server, read tools only for now.

Two sources, chosen by environment:

- `ABOGI_CALENDAR_TOOLBOX=<name>` with `FOUNDRY_PROJECT_ENDPOINT`: a Foundry
  Toolbox that fronts a calendar MCP server and holds the owner's Google
  credentials. Abogi authenticates to Foundry with azure-identity
  (scope `https://ai.azure.com/.default`).
- `ABOGI_CALENDAR_MCP_URL` (+ optional `ABOGI_CALENDAR_MCP_TOKEN`): any
  streamable-HTTP MCP server, for local development, such as a local
  `nspady/google-calendar-mcp` or Google's remote calendar MCP server.

Abogi never takes an outward step without approval, and approvals do not
exist yet, so the model only sees tools that `is_read_only` allows: names
with a read verb (list, get, search, ...) and no write verb (create, update,
delete, respond, ...). Unknown names are hidden: this is an allow-list.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import anyio

from abogi.connectors.mcp_connector import McpConnector, TokenProvider

FOUNDRY_SCOPE = "https://ai.azure.com/.default"

READ_VERBS = frozenset(
    {"list", "get", "search", "query", "find", "freebusy", "suggest", "read", "lookup"}
)
WRITE_VERBS = frozenset(
    {
        "accept", "add", "cancel", "change", "clear", "copy", "create", "decline",
        "delete", "edit", "import", "insert", "invite", "modify", "move", "patch",
        "post", "put", "quick", "remove", "rename", "respond", "rsvp", "send", "set",
        "share", "stop", "subscribe", "update", "watch", "write",
    }
)  # fmt: skip

_WORDS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def is_read_only(tool_name: str) -> bool:
    """True when the model may see this calendar tool without an approval step."""
    words = {word.lower() for word in _WORDS.findall(tool_name)}
    return bool(words & READ_VERBS) and not words & WRITE_VERBS


def toolbox_url(project_endpoint: str, toolbox: str) -> str:
    return f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox}/mcp?api-version=v1"


def foundry_token() -> TokenProvider:
    """A bearer-token provider for Foundry from the ambient Azure identity.

    Uses the synchronous credential on a worker thread so the provider works
    from any event loop; azure-identity caches and refreshes the token.
    """
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    get_token = get_bearer_token_provider(DefaultAzureCredential(), FOUNDRY_SCOPE)

    async def provider() -> str:
        return await anyio.to_thread.run_sync(get_token)

    return provider


def static_token(token: str) -> TokenProvider:
    async def provider() -> str:
        return token

    return provider


def build_calendar_connector(env: Mapping[str, str]) -> McpConnector | None:
    """The configured calendar connector, or None when none is configured."""
    toolbox = env.get("ABOGI_CALENDAR_TOOLBOX", "").strip()
    url = env.get("ABOGI_CALENDAR_MCP_URL", "").strip()

    if toolbox and url:
        raise ValueError(
            "Set ABOGI_CALENDAR_TOOLBOX or ABOGI_CALENDAR_MCP_URL, not both."
        )
    if toolbox:
        endpoint = env.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError("ABOGI_CALENDAR_TOOLBOX needs FOUNDRY_PROJECT_ENDPOINT.")
        return McpConnector.over_http(
            "calendar", toolbox_url(endpoint, toolbox), foundry_token(), allow=is_read_only
        )
    if url:
        token = env.get("ABOGI_CALENDAR_MCP_TOKEN", "").strip()
        return McpConnector.over_http(
            "calendar", url, static_token(token) if token else None, allow=is_read_only
        )
    return None
