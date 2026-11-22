"""Keep one conversation with the hosted agent warm for a voice session.

A new hosted-agent conversation spends ~6 s in Foundry starting a session
before Abogi's code runs; follow-ups in the same conversation do not
(.scratch/step-2-voice/plan.md). So a voice client keeps a single chained
conversation for as long as it runs and sends a short warm-up turn into that
same chain whenever the session may have gone cold: at start, and on
push-to-talk key-down after a long idle, so the reheat overlaps the owner
speaking.

Thread-safe: `warm_in_background()` may run while `ask()` is called; `ask()`
waits for the warm-up so the chain's `previous_response_id` stays in order.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

# Sent as the first turn of a session. The reply is thrown away.
WARM_UP = "Voice session starting. Reply with one word: ready."

# Re-warm a little before the server's idle timeout, not after it.
MARGIN_SECONDS = 60.0


class Asks(Protocol):
    def ask(self, text: str) -> str: ...


class WarmConversation:
    def __init__(
        self,
        conversation: Asks,
        idle_timeout: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        warm_up: str = WARM_UP,
    ) -> None:
        self._conversation = conversation
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._warm_up = warm_up
        self._last_turn: float | None = None
        self._lock = threading.Lock()

    def is_cold(self) -> bool:
        if self._last_turn is None:
            return True
        return self._clock() - self._last_turn > max(0.0, self._idle_timeout - MARGIN_SECONDS)

    def warm(self) -> bool:
        """Send the warm-up turn if the session may be cold. True if one was sent."""
        with self._lock:
            if not self.is_cold():
                return False
            self._conversation.ask(self._warm_up)
            self._last_turn = self._clock()
            return True

    def warm_in_background(self) -> threading.Thread | None:
        """Start warming without blocking (for key-down). None if already warm."""
        if not self.is_cold():
            return None
        thread = threading.Thread(target=self._warm_quietly, name="abogi-warm", daemon=True)
        thread.start()
        return thread

    def _warm_quietly(self) -> None:
        try:
            self.warm()
        except Exception:  # noqa: BLE001 - a failed warm-up must not kill the client;
            pass  # the real question will pay the cold start and surface the error.

    def ask(self, text: str) -> str:
        with self._lock:
            answer = self._conversation.ask(text)
            self._last_turn = self._clock()
            return answer
