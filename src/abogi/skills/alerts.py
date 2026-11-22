"""Alerts: what is worth interrupting the owner for right now (step 4).

An alert check is a plain request to the hosted agent whose input is exactly
`ALERT_CHECK`. It is answered by code, with no model call, so the tray client
can ask every few minutes for the cost of a couple of Google requests. The
answer is JSON: `{"alerts": [{"id": ..., "text": ...}]}`. The client remembers
which ids it has spoken, so each alert is said once.

What counts (the owner's urgency setting, from the environment):
- a timed event starting within `ABOGI_ALERT_MINUTES` (default 10);
- unread mail from anyone in `ABOGI_ALERT_FROM` (comma-separated addresses),
  received in the last day;
- a failed CI run on the default branch of a repository in `ABOGI_GITHUB_REPOS`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

ALERT_CHECK = "abogi:alert-check"
DEFAULT_MINUTES = 10


def is_alert_check(items: list[dict[str, Any]]) -> bool:
    """True when this request's last owner message is exactly the marker."""
    users = [item for item in items if item.get("role") == "user"]
    return bool(users) and str(users[-1].get("content", "")).strip() == ALERT_CHECK


def settings(env: Mapping[str, str]) -> tuple[int, list[str]]:
    try:
        minutes = max(1, int(env.get("ABOGI_ALERT_MINUTES") or DEFAULT_MINUTES))
    except ValueError:
        minutes = DEFAULT_MINUTES
    senders = [s.strip() for s in (env.get("ABOGI_ALERT_FROM") or "").split(",") if s.strip()]
    return minutes, senders


async def collect(connectors: list[Any], env: Mapping[str, str], now: datetime) -> list[dict[str, str]]:
    """Alerts from every connector that can give them; a failing one is skipped."""
    minutes, senders = settings(env)
    jobs = []
    for connector in connectors:
        if hasattr(connector, "upcoming_alerts"):
            jobs.append(connector.upcoming_alerts(now, minutes))
        if senders and hasattr(connector, "mail_alerts"):
            jobs.append(connector.mail_alerts(senders))
        if hasattr(connector, "upkeep_alerts"):
            jobs.append(connector.upkeep_alerts())
    alerts: list[dict[str, str]] = []
    for result in await asyncio.gather(*jobs, return_exceptions=True):
        if isinstance(result, BaseException):
            logger.warning("Alert check failed for one source: %s: %s", type(result).__name__, result)
            continue
        alerts.extend(result)
    return alerts


def answer(alerts: list[dict[str, str]]) -> str:
    return json.dumps({"alerts": alerts}, ensure_ascii=False)
