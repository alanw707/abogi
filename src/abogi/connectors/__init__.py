"""Connectors: access to external systems, offered to the loop as tools.

`load_tools()` builds every connector configured in the environment and
returns their tools: the calendar (see `calendar.py`), Gmail
(`google_mail.py`), GitHub (`github.py`) and Discord servers (`discord.py`).

Lifetime: the returned tools hold no open connection. Each tool call opens
its own short MCP session (see `mcp_connector.py`), so `load_tools()` can be
called once at startup and the tools reused for every turn. Call it again to
pick up tools a server added since. A connector with a `prepare()` coroutine
gets it started in the background here, so slow first-use work (such as the
hosted agent reading the Google sign-in from Key Vault) overlaps the first
turn's model call instead of the first tool call.

Failure policy: a configuration mistake (such as both calendar sources set)
raises, because the owner must fix it. A connector that cannot be reached is
logged and skipped, so Abogi still starts and can say what it lacks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from abogi.connectors.calendar import build_calendar_connector
from abogi.connectors.discord import build_discord_connector
from abogi.connectors.github import build_github_connector
from abogi.connectors.google_mail import build_mail_connector
from abogi.connectors.mcp_connector import McpConnector
from abogi.core.loop import Tool

__all__ = ["McpConnector", "load_connectors", "load_tools"]

logger = logging.getLogger(__name__)


_built: list[object] | None = None


def load_connectors() -> list[object]:
    """Every configured connector, built once per process (their token
    providers cache the sign-in, so one instance each is shared)."""
    global _built
    if _built is None:
        made = (
            build_calendar_connector(os.environ),
            build_mail_connector(os.environ),
            build_github_connector(os.environ),
            build_discord_connector(os.environ),
        )
        _built = [c for c in made if c is not None]
    return list(_built)


async def load_tools() -> list[Tool]:
    """The tools of every configured connector; [] when none is configured."""
    connectors = load_connectors()
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
            continue
        prepare = getattr(connector, "prepare", None)
        if prepare is not None:
            _start_preparing(connector.name, prepare)
    return tools


_preparing: set[asyncio.Task[None]] = set()  # keep references so tasks are not collected


def _start_preparing(name: str, prepare: Callable[[], Awaitable[None]]) -> None:
    """Run a connector's first-use work alongside the turn's model call.

    A failure is only logged: the first real tool call will redo the work and
    report the error to the model then.
    """

    async def run() -> None:
        try:
            await prepare()
        except Exception as error:  # noqa: BLE001
            logger.warning("Preparing connector %s failed: %s: %s", name, type(error).__name__, error)

    task = asyncio.get_running_loop().create_task(run())
    _preparing.add(task)
    task.add_done_callback(_preparing.discard)
