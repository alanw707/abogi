"""Approvals for outward steps, enforced by the core (ADR 0004).

The loop never runs an outward tool itself. It hands the call to `hold()`,
which keeps it as the conversation's one pending step, and tells the model to
read the step back. On the owner's next turn the channel calls `take()`: if
that turn `is_approval()`, the step is run by the core; otherwise it is
dropped. So one approval covers exactly the step just read back, and text the
model reads (an email, a web page) cannot cause a step to run.

Pending steps live in memory for `ttl` seconds. Losing one is safe: the owner
is told nothing is waiting and asks again.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from abogi.core.loop import ToolCall

APPROVAL_PHRASE = "Abogi, approve"
TTL_SECONDS = 600.0

_NEGATIONS = {"no", "not", "dont", "don't", "never", "cancel", "stop", "wait"}
_APPROVE = {"approve", "approved"}


def is_approval(text: str) -> bool:
    """True when the owner's turn is the approval phrase.

    Lenient because it arrives through speech recognition, which may spell the
    name "A bogey" or drop it: the turn must be short (at most four words), end
    in "approve" or "approved", and carry no negation. A plain "yes" is not
    an approval, so a stray "yes" in hands-free mode sends nothing.
    """
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 4 or words[-1] not in _APPROVE:
        return False
    return not _NEGATIONS.intersection(words)


@dataclass(frozen=True)
class Pending:
    call: ToolCall
    read_back: str  # the sentence the owner heard
    held_at: float


class Approvals:
    """The pending outward step of each conversation, keyed by a conversation id."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl: float = TTL_SECONDS) -> None:
        self._clock = clock
        self._ttl = ttl
        self._pending: dict[str, Pending] = {}

    def hold(self, key: str, call: ToolCall, read_back: str) -> None:
        """Keep `call` as the pending step, replacing any earlier one."""
        self._sweep()
        self._pending[key] = Pending(call, read_back, self._clock())

    def take(self, key: str) -> Pending | None:
        """Remove and return the pending step, if one is waiting and not expired."""
        self._sweep()
        return self._pending.pop(key, None)

    def _sweep(self) -> None:
        cutoff = self._clock() - self._ttl
        for key in [k for k, p in self._pending.items() if p.held_at < cutoff]:
            del self._pending[key]
