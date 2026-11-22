"""When Abogi speaks first: briefings and alerts, without audio or OS code (step 4).

- A **briefing** is due at each time in `ABOGI_BRIEFING_TIMES` (local, such
  as "07:30,17:45"), once a day each. It fires only within `GRACE` of its time,
  so starting the tray at noon does not trigger the morning briefing.
- **Alerts** come from the hosted agent's code-only alert check (see
  `abogi.skills.alerts`). `AlertQueue` says each one once, even though every
  check returns it again until it is no longer current.

The tray client speaks either by sending a prompt into the voice
conversation, marked so the agent knows it did not come from the owner.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import date, datetime, time, timedelta

GRACE = timedelta(minutes=30)
BRIEFING_PROMPT = "(Abogi briefing) Give me my briefing."
ALERT_PREFIX = "(Abogi alert) Tell me this in one short sentence: "


def parse_times(text: str) -> list[time]:
    """ "07:30, 17:45" -> [time(7, 30), time(17, 45)]; bad entries are ignored."""
    times = []
    for part in (text or "").split(","):
        try:
            times.append(time.fromisoformat(part.strip()))
        except ValueError:
            continue
    return sorted(times)


class Briefings:
    def __init__(self, times: list[time]) -> None:
        self._times = times
        self._done: set[tuple[date, time]] = set()

    def due(self, now: datetime) -> bool:
        """True once for each briefing time reached in the last `GRACE`."""
        for at in self._times:
            when = datetime.combine(now.date(), at, now.tzinfo)
            if when <= now < when + GRACE and (now.date(), at) not in self._done:
                self._done.add((now.date(), at))
                return True
        return False


class AlertQueue:
    """Alerts waiting to be spoken, each id at most once."""

    def __init__(self, remember: int = 500) -> None:
        self._seen: deque[str] = deque(maxlen=remember)
        self._waiting: deque[str] = deque()

    def add(self, alerts: list[dict[str, str]]) -> int:
        added = 0
        for alert in alerts:
            key, text = alert.get("id"), alert.get("text")
            if key and text and key not in self._seen:
                self._seen.append(key)
                self._waiting.append(text)
                added += 1
        return added

    def pop(self) -> str | None:
        return self._waiting.popleft() if self._waiting else None


def parse_alerts(answer: str) -> list[dict[str, str]]:
    """The alert check's JSON answer; anything unreadable counts as none."""
    try:
        alerts = json.loads(answer).get("alerts", [])
    except (ValueError, AttributeError):
        return []
    return [a for a in alerts if isinstance(a, dict)]


def alert_prompt(text: str) -> str:
    return ALERT_PREFIX + text
