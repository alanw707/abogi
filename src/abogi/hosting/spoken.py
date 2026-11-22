"""A streamed answer split into messages at each pause, so a lead-in is spoken at once.

Voice Live held back a short lead-in ("Let me look that up.") until the text
after it arrived, which after a 40 s research call meant 40 s of silence. The
loop yields a `Pause` when a step that calls tools ends; here that closes the
current output message, so the lead-in is a finished message before the tools
run, and the rest of the answer comes as a new message.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator, Callable
from typing import Any

from azure.ai.agentserver.responses.streaming import ResponseEventStream

from abogi.core.loop import Pause


_CITATION = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")  # " ([python.org](https://...))"
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # "[python.org](https://...)" -> "python.org"
_URL = re.compile(r"https?://\S+")
_HOLD = 400  # never hold back more than this while waiting for a link to close


def speakable_text(text: str) -> str:
    return _URL.sub("", _LINK.sub(r"\1", _CITATION.sub("", text)))


async def speakable(chunks: AsyncIterable[str]) -> AsyncIterator[str]:
    """Drop citation links and URLs the web search adds, which would be read aloud.

    Text after an unclosed "[" or "(" is held back until it closes (or grows
    past `_HOLD`), so a link split across chunks is still recognised.
    """
    held = ""
    async for chunk in chunks:
        if isinstance(chunk, Pause):
            if held:
                yield speakable_text(held)
                held = ""
            yield chunk
            continue
        held += chunk
        cut = _open_from(held)
        if cut is None or len(held) - cut > _HOLD:
            cut = len(held)
        ready, held = held[:cut], held[cut:]
        trimmed = ready.rstrip()
        if trimmed != ready and held == "":
            # Keep trailing spaces back: a citation may follow (" ([site](url))").
            ready, held = trimmed, ready[len(trimmed):]
        elif trimmed != ready:
            ready, held = trimmed, ready[len(trimmed):] + held
        if ready:
            ready = speakable_text(ready)
            if ready:
                yield ready
    if held:
        tail = speakable_text(held)
        if tail:
            yield tail


def _open_from(text: str) -> int | None:
    """Index of the earliest "[" or "(" not yet closed, if any."""
    depth, start = 0, None
    for index, char in enumerate(text):
        if char in "[(":
            if depth == 0:
                start = index
            depth += 1
        elif char in "])" and depth:
            depth -= 1
            if depth == 0:
                start = None
    return start if depth else None


class SpokenResponse:
    """Returned from a response handler; yields Responses stream events."""

    def __init__(
        self,
        context: Any,
        request: Any,
        *,
        text: AsyncIterable[str],
        configure: Callable[[Any], None] | None = None,
    ) -> None:
        self._context = context
        self._request = request
        self._text = text
        self._configure = configure

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[Any]:
        stream = ResponseEventStream(response_id=self._context.response_id, request=self._request)
        if self._configure is not None:
            self._configure(stream.response)
        yield stream.emit_created()
        yield stream.emit_in_progress()

        message = content = None
        said: list[str] = []

        def close() -> list[Any]:
            nonlocal message, content, said
            if message is None:
                return []
            events = [content.emit_text_done("".join(said)), content.emit_done(), message.emit_done()]
            message, content, said = None, None, []
            return events

        async for chunk in self._text:
            if isinstance(chunk, Pause):
                for event in close():
                    yield event
                continue
            if not chunk:
                continue
            if message is None:
                message = stream.add_output_item_message()
                yield message.emit_added()
                content = message.add_text_content()
                yield content.emit_added()
            said.append(chunk)
            yield content.emit_delta(chunk)

        if message is None and not said:  # nothing said at all: still one (empty) message
            message = stream.add_output_item_message()
            yield message.emit_added()
            content = message.add_text_content()
            yield content.emit_added()
        for event in close():
            yield event
        yield stream.emit_completed()
