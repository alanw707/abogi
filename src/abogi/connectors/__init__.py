"""Connectors: access to external systems, offered to the loop as tools.

`load_tools()` builds every connector configured in the environment and
returns their tools. Today that is only the calendar (see `calendar.py`).

Lifetime: the returned tools hold no open connection. Each tool call opens
its own short MCP session (see `mcp_connector.py`), so `load_tools()` can be
called once at startup and the tools reused for every turn. Call it again to
pick up tools a server added since.

Failure policy: a configuration mistake (such as both calendar sources set)
raises, because the owner must fix it. A connector that cannot be reached is
logged and skipped, so Abogi still starts and can say what it lacks.
"""

from __future__ import annotations

import logging
import os

from abogi.connectors.calendar import build_calendar_connector
from abogi.connectors.mcp_connector import McpConnector
from abogi.core.loop import Tool

__all__ = ["McpConnector", "load_tools"]

logger = logging.getLogger(__name__)


async def load_tools() -> list[Tool]:
    """The tools of every configured connector; [] when none is configured."""
    connectors = [c for c in (build_calendar_connector(os.environ),) if c is not None]
    tools: list[Tool] = []
    for connector in connectors:
        try:
            tools.extend(await connector.tools())
        except Exception as error:  # noqa: BLE001 - one bad connector must not stop the agent
            logger.warning(
                "Connector %s at %s is unavailable and was skipped: %s: %s",
                connector.name,
                connector.url,
                type(error).__name__,
                error,
            )
    return tools
