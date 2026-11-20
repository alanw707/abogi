"""Push-to-talk over Voice Live, without any audio or OS code (step 2).

`VoiceSession` turns four inputs into Voice Live client events:

- `key_down()`: start a turn. If Abogi is thinking or speaking, that answer
  is cancelled and its queued audio dropped (barge-in).
- `audio(pcm)`: microphone chunks, sent only while the key is held.
- `key_up()`: end the turn: commit the audio and ask for a response. A tap
  shorter than `min_press` is thrown away.
- `handle(event)`: server events: reply audio goes to the player, and the
  state goes back to idle when the current response is done.

The session also warms itself: a silent text-only turn at start and when it
may have gone cold (hosted sessions idle out after 900 s by default), so a
spoken question does not pay the agent's ~6 s session start. Voice Live
allows one active response at a time, so a key released during a warm-up
commits its audio and asks for the response once the warm-up is done.

The Windows wiring (microphone, speaker, hotkey, tray icon) is in `tray.py`.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from abogi.channels.warm import MARGIN_SECONDS, WARM_UP

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000  # pcm16 mono both ways, the Voice Live default
DEFAULT_VOICE = "en-US-Ava:DragonHDLatestNeural"
IDLE_TIMEOUT_SECONDS = 900  # hosted agent session default

IDLE, WARMING, LISTENING, THINKING, SPEAKING = "idle", "warming", "listening", "thinking", "speaking"


class Player(Protocol):
    def play(self, pcm: bytes) -> None: ...
    def flush(self) -> None: ...


def session_config(voice: str = DEFAULT_VOICE, language: str = "en") -> dict[str, Any]:
    """Agent-mode session settings. No `instructions`: the persona is the agent's."""
    return {
        "modalities": ["text", "audio"],
        "voice": {"name": voice, "type": "azure-standard"},
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "input_audio_transcription": {"model": "azure-speech", "language": language},
        "turn_detection": None,  # push-to-talk: the key says when a turn ends
    }


def _kind(event: Any) -> str:
    kind = getattr(event, "type", "")
    return str(getattr(kind, "value", kind))


class VoiceSession:
    def __init__(
        self,
        connection: Any,
        player: Player,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        min_press: float = 0.25,
        on_state: Callable[[str], None] = lambda state: None,
        on_text: Callable[[str, str], None] = lambda who, text: None,
    ) -> None:
        self._connection = connection
        self._player = player
        self._clock = clock
        self._idle_timeout = idle_timeout
        self._min_press = min_press
        self._on_state = on_state
        self._on_text = on_text

        self.state = IDLE
        self.conversation_id: str | None = None
        self._last_turn: float | None = None
        self._pressed_at = 0.0
        self._active_id: str | None = None  # the reply being thought about or spoken
        self._awaiting_reply = False  # response.create sent, response.created not seen yet
        self._drop_next_created = False  # that reply was cancelled before it was created
        self._muted: set[str] = set()  # cancelled replies whose late events we ignore
        self._warming = False  # a warm-up response is in flight
        self._warm_id: str | None = None
        self._pending_reply = False  # audio committed, waiting for the warm-up to finish

    # --- inputs from the client ----------------------------------------

    def is_cold(self) -> bool:
        if self._last_turn is None:
            return True
        return self._clock() - self._last_turn > max(0.0, self._idle_timeout - MARGIN_SECONDS)

    async def warm(self) -> bool:
        """Send a silent warm-up turn if the session may be cold and nothing is running."""
        if self._warming or self.state not in (IDLE, LISTENING) or not self.is_cold():
            return False
        self._warming = True
        await self._send_text(WARM_UP)
        await self._connection.response.create(response={"modalities": ["text"]})
        if self.state == IDLE:
            self._set(WARMING)
        return True

    async def key_down(self) -> None:
        if self.state == LISTENING:
            return  # key repeat
        if self.state in (THINKING, SPEAKING):
            await self._cancel_current()
        self._pending_reply = False
        await self._connection.input_audio_buffer.clear()
        self._pressed_at = self._clock()
        self._set(LISTENING)
        await self.warm()  # reheats while the owner speaks, if it went cold

    async def audio(self, pcm: bytes) -> None:
        if self.state == LISTENING:
            await self._connection.input_audio_buffer.append(audio=base64.b64encode(pcm).decode())

    async def key_up(self) -> None:
        if self.state != LISTENING:
            return
        if self._clock() - self._pressed_at < self._min_press:
            await self._connection.input_audio_buffer.clear()
            self._set(WARMING if self._warming else IDLE)
            return
        await self._connection.input_audio_buffer.commit()
        self._set(THINKING)
        if self._warming:
            self._pending_reply = True
        else:
            await self._ask_for_reply()

    # --- server events ---------------------------------------------------

    async def handle(self, event: Any) -> None:
        kind = _kind(event)
        response = getattr(event, "response", None)
        response_id = getattr(event, "response_id", None) or getattr(response, "id", None)

        if kind == "response.created":
            self.conversation_id = getattr(response, "conversation_id", None) or self.conversation_id
            if self._awaiting_reply:
                self._awaiting_reply = False
                if self._drop_next_created:
                    self._drop_next_created = False
                    if response_id:
                        self._muted.add(response_id)
                        await self._connection.response.cancel(response_id=response_id)
                else:
                    self._active_id = response_id
            elif self._warming:
                self._warm_id = response_id
        elif kind == "response.audio.delta":
            if response_id in self._muted or (response_id and response_id != self._active_id):
                return
            if self.state == THINKING:
                self._set(SPEAKING)
            if self.state == SPEAKING:
                delta = event.delta
                self._player.play(delta if isinstance(delta, bytes) else base64.b64decode(delta))
        elif kind == "response.audio_transcript.done" and response_id not in self._muted:
            self._on_text("abogi", getattr(event, "transcript", "") or "")
        elif kind == "conversation.item.input_audio_transcription.completed":
            self._on_text("owner", getattr(event, "transcript", "") or "")
        elif kind == "response.done":
            await self._response_done(response_id)
        elif kind == "error":
            error = getattr(event, "error", None)
            logger.warning("Voice Live error: %s", getattr(error, "message", error))
            if self.state in (THINKING, SPEAKING, WARMING):
                self._warming = self._pending_reply = self._awaiting_reply = False
                self._active_id = self._warm_id = None
                self._set(IDLE)

    async def _response_done(self, response_id: str | None) -> None:
        if response_id in self._muted:
            self._muted.discard(response_id)
            return
        self._last_turn = self._clock()
        if self._warming and (response_id is None or response_id == self._warm_id):
            self._warming = False
            self._warm_id = None
            if self._pending_reply:
                self._pending_reply = False
                await self._ask_for_reply()
            elif self.state == WARMING:
                self._set(IDLE)
            return
        if response_id is None or response_id == self._active_id:
            self._active_id = None
            if self.state in (THINKING, SPEAKING):
                self._set(IDLE)

    # --- helpers -----------------------------------------------------------

    async def _ask_for_reply(self) -> None:
        self._active_id = None
        self._awaiting_reply = True
        await self._connection.response.create()

    async def _cancel_current(self) -> None:
        self._player.flush()
        if self._active_id:
            self._muted.add(self._active_id)
            await self._connection.response.cancel(response_id=self._active_id)
            self._active_id = None
        elif self._awaiting_reply:
            self._drop_next_created = True  # cancel it as soon as it has an id
        # else: the reply was still waiting on a warm-up; key_down drops that wait.

    async def _send_text(self, text: str) -> None:
        from azure.ai.voicelive.models import InputTextContentPart, UserMessageItem

        await self._connection.conversation.item.create(
            item=UserMessageItem(content=[InputTextContentPart(text=text)])
        )

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._on_state(state)
