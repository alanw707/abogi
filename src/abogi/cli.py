"""Terminal channel: talk to Abogi over text.

    python -m abogi.cli "what's on my calendar tomorrow"   # one message
    python -m abogi.cli                                    # REPL
    python -m abogi.cli --agent abogi "..."                # hosted agent

Targets:

* ``--local`` (default): the agent server running on this machine. The
  azure-ai-agentserver-responses host serves ``POST /responses`` with no
  prefix on port 8088, so the OpenAI client's ``base_url`` is the bare origin.
* ``--agent NAME``: a Foundry hosted agent, reached through
  ``AIProjectClient.get_openai_client(agent_name=NAME)``. The project endpoint
  comes from ``ABOGI_PROJECT_ENDPOINT``; sign-in uses DefaultAzureCredential.

Conversation state: each turn sends only the new message plus
``previous_response_id`` from the last reply, so the server keeps history.
If a server ever replies without an id, the client falls back to resending
the whole history itself.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from typing import Any

DEFAULT_LOCAL_URL = "http://localhost:8088"
ENDPOINT_ENV = "ABOGI_PROJECT_ENDPOINT"
# Until the hosted session's real idle timeout is confirmed, assume 15 minutes.
WARM_IDLE_SECONDS = 15 * 60


class Conversation:
    """One running conversation with the agent."""

    def __init__(self, client: Any, model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._previous_id: str | None = None
        self._history: list[dict[str, str]] | None = None  # only used without ids

    def ask(self, text: str) -> str:
        message = {"role": "user", "content": text}
        kwargs: dict[str, Any] = {}
        if self._model:
            kwargs["model"] = self._model
        if self._history is not None:
            self._history.append(message)
            kwargs["input"] = list(self._history)
        else:
            kwargs["input"] = [message]
            if self._previous_id:
                kwargs["previous_response_id"] = self._previous_id

        response = self._client.responses.create(**kwargs)
        answer = response.output_text or ""

        response_id = getattr(response, "id", None)
        if self._history is None and response_id:
            self._previous_id = response_id
        else:
            if self._history is None:  # first reply without an id: switch modes
                self._history = [message]
            self._history.append({"role": "assistant", "content": answer})
        return answer


def make_client(args: argparse.Namespace) -> Any:
    if args.agent:
        endpoint = os.environ.get(ENDPOINT_ENV)
        if not endpoint:
            raise SystemExit(f"{ENDPOINT_ENV} is not set; it must be the Foundry project endpoint.")
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        # agent_name needs allow_preview=True, or get_openai_client raises ValueError.
        project = AIProjectClient(
            endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True
        )
        return project.get_openai_client(agent_name=args.agent)

    from openai import OpenAI

    # The local agent server does not check the key, but the client requires one.
    return OpenAI(base_url=args.url, api_key="local")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="abogi", description="Talk to Abogi from the terminal.")
    parser.add_argument("message", nargs="?", help="send one message and exit; omit for a REPL")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true", help="use the local agent server (default)")
    target.add_argument("--agent", metavar="NAME", help="use the Foundry hosted agent NAME")
    parser.add_argument("--url", default=DEFAULT_LOCAL_URL, help="local agent server URL")
    parser.add_argument("--model", help="model name to send, if the server wants one")
    parser.add_argument(
        "--warm",
        action="store_true",
        help="REPL only: send a warm-up turn first, so questions skip the session start",
    )
    parser.add_argument("--time", action="store_true", help="print how long each reply took")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[argparse.Namespace], Any] = make_client,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> int:
    import time

    from abogi.channels.warm import WarmConversation

    args = parse_args(argv)
    conversation: Any = Conversation(client_factory(args), model=args.model)

    def timed(label: str, action: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        result = action()
        if args.time:
            write(f"({label}: {time.perf_counter() - started:.1f}s)")
        return result

    if args.message:
        write(timed("reply", lambda: conversation.ask(args.message)))
        return 0

    if args.warm:
        conversation = WarmConversation(conversation, idle_timeout=WARM_IDLE_SECONDS)
        timed("warm-up", conversation.warm)

    while True:
        try:
            text = read("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            return 0
        if text in {"exit", "quit"}:
            return 0
        if text:
            if args.warm:
                conversation.warm()  # only sends anything after a long idle
            write(timed("reply", lambda text=text: conversation.ask(text)))


def cli() -> None:
    """Console-script entry point: one-line errors instead of tracebacks."""
    from openai import APIError

    try:
        sys.exit(main())
    except APIError as err:
        sys.exit(f"abogi: {err}")


if __name__ == "__main__":
    cli()
