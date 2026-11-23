"""Discord: reach Abogi by direct message, or by tagging it in a server channel (step 4).

Run with `uv run --extra discord --env-file .env python -m abogi.channels.discord`
anywhere that can sign in to Azure (it calls the hosted agent like the CLI).

- Only the owner (`ABOGI_DISCORD_OWNER_ID`) is answered: in a direct message,
  or in a server channel when the owner tags the bot. Everyone else, and every
  other bot, is ignored, so the bot is safe to leave running. A reply in a
  server channel is visible to that server and pings no one.
- Each channel is one conversation, chained by `previous_response_id`, so
  approvals work as they do by voice: Abogi reads the step back, and typing
  "Abogi, approve" runs it (ADR 0004).
- With `ABOGI_DISCORD_ALERTS=on` it also sends alerts (the same code-only
  check the tray uses) every `ABOGI_ALERT_EVERY` seconds. Turn it on where the
  tray is not running, or alerts arrive twice.

Needs a Discord application with a bot user (`ABOGI_DISCORD_TOKEN`) and the
Message Content intent, invited to a server the owner is in so DMs can start.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from typing import Any

from abogi.channels.proactive import AlertQueue, parse_alerts
from abogi.skills.alerts import ALERT_CHECK

logger = logging.getLogger("abogi.discord")

LIMIT = 2000  # Discord's message length limit


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Split a reply into Discord-sized pieces, preferring line then word breaks."""
    text = text.strip() or "(no answer)"
    pieces = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def without_tag(text: str, bot_id: int) -> str:
    """The message with the bot's own tag (`<@id>` or `<@!id>`) removed."""
    return re.sub(rf"<@!?{bot_id}>", " ", text).strip()


class Relay:
    """The owner's messages to the hosted agent and back, one conversation per channel.

    `agent` is an OpenAI-style client for the hosted agent (blocking); calls
    run in a thread so the Discord connection keeps its heartbeat.
    """

    def __init__(self, agent: Any, owner_id: int) -> None:
        self._agent = agent
        self._owner_id = owner_id
        self._previous: dict[int, str] = {}  # channel id -> last response id
        self._locks: dict[int, asyncio.Lock] = {}

    def accepts(self, author_id: int, is_dm: bool, is_bot: bool, tagged: bool = False) -> bool:
        """The owner, in a direct message or tagging the bot in a server channel."""
        return (is_dm or tagged) and not is_bot and author_id == self._owner_id

    async def answer(self, channel_id: int, text: str) -> str:
        lock = self._locks.setdefault(channel_id, asyncio.Lock())
        async with lock:  # one turn at a time per conversation
            kwargs: dict[str, Any] = {"input": text}
            if channel_id in self._previous:
                kwargs["previous_response_id"] = self._previous[channel_id]
            response = await asyncio.to_thread(self._agent.responses.create, **kwargs)
            if getattr(response, "id", None):
                self._previous[channel_id] = response.id
            return response.output_text or ""

    async def check_alerts(self, queue: AlertQueue) -> list[str]:
        response = await asyncio.to_thread(self._agent.responses.create, input=ALERT_CHECK)
        queue.add(parse_alerts(response.output_text or ""))
        texts = []
        while (text := queue.pop()) is not None:
            texts.append(text)
        return texts


def make_agent_client(endpoint: str, agent_name: str) -> Any:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True)
    return project.get_openai_client(agent_name=agent_name)


def run(
    token: str,
    relay: Relay,
    owner_id: int,
    *,
    alert_every: float = 0.0,
    client_factory: Callable[..., Any] | None = None,
) -> None:
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    client = (client_factory or discord.Client)(intents=intents)

    async def send_alerts() -> None:
        await client.wait_until_ready()
        owner = await client.fetch_user(owner_id)
        queue = AlertQueue()
        while not client.is_closed():
            try:
                for text in await relay.check_alerts(queue):
                    await owner.send(f"Alert: {text}")
            except Exception as error:  # noqa: BLE001 - keep the bot up
                logger.warning("Alert check failed: %s: %s", type(error).__name__, error)
            await asyncio.sleep(alert_every)

    @client.event
    async def on_ready() -> None:
        logger.info("Discord connected as %s; answering the owner's DMs and tags only.", client.user)
        if alert_every:
            client.loop.create_task(send_alerts())

    @client.event
    async def on_message(message: Any) -> None:
        is_dm = isinstance(message.channel, discord.DMChannel)
        tagged = client.user in message.mentions  # the bot itself, not @everyone or a role
        if not relay.accepts(message.author.id, is_dm, message.author.bot, tagged):
            return
        text = without_tag(message.content, client.user.id)
        if not text:
            return
        async with message.channel.typing():
            try:
                reply = await relay.answer(message.channel.id, text)
            except Exception as error:  # noqa: BLE001 - tell the owner, keep the bot up
                logger.warning("Agent call failed: %s: %s", type(error).__name__, error)
                reply = f"I could not reach Abogi just now ({type(error).__name__})."
        for piece in split_message(reply):
            await message.channel.send(piece, allowed_mentions=discord.AllowedMentions.none())

    client.run(token, log_handler=None)


def main() -> None:
    logging.basicConfig(level=os.environ.get("ABOGI_LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("ABOGI_DISCORD_TOKEN", "").strip()
    owner = os.environ.get("ABOGI_DISCORD_OWNER_ID", "").strip()
    if not token or not owner.isdigit():
        raise SystemExit("Set ABOGI_DISCORD_TOKEN and ABOGI_DISCORD_OWNER_ID (your numeric Discord user id).")
    agent = make_agent_client(os.environ["ABOGI_PROJECT_ENDPOINT"], os.environ.get("ABOGI_AGENT_NAME", "abogi"))
    alerts_on = os.environ.get("ABOGI_DISCORD_ALERTS", "").lower() == "on"
    every = float(os.environ.get("ABOGI_ALERT_EVERY") or 300) if alerts_on else 0.0
    run(token, Relay(agent, int(owner)), int(owner), alert_every=every)


if __name__ == "__main__":
    main()
