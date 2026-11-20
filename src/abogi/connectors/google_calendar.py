"""Google Calendar through Google's regular Calendar API (v3), read-only.

Google's remote Calendar MCP server needs the Cloud project enrolled in the
Workspace Developer Preview Program, so this connector calls the GA REST API
directly instead (ADR 0003). It offers the same kind of read tools, built for
a spoken assistant: dates instead of RFC 3339 ranges, and short plain-text
results.

Selected with `ABOGI_CALENDAR_API=google`; uses the owner's saved Google
sign-in (`python -m abogi.connectors.google_auth`).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from abogi.connectors.mcp_connector import TokenProvider
from abogi.core.loop import Tool

API = "https://www.googleapis.com/calendar/v3"
MAX_EVENTS = 50

Fetch = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def http_fetch(token: TokenProvider, timeout: float = 20.0) -> Fetch:
    """GET a Calendar API path with the owner's bearer token."""

    async def fetch(path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {await token()}"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{API}{path}", params=params, headers=headers)
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:200]
            raise RuntimeError(f"Google Calendar said {response.status_code}: {message}")
        return response.json()

    return fetch


class GoogleCalendarConnector:
    name = "calendar"
    url = API

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch
        self._zones: dict[str, str] = {}  # calendar time zones rarely change; one lookup each

    async def prepare(self) -> None:
        """Do the slow first-use work early: load and refresh the Google sign-in
        (from Key Vault when hosted) and look up the primary calendar's time zone."""
        await self._time_zone("primary")

    async def tools(self) -> list[Tool]:
        return [
            Tool(
                "list_calendars",
                "List the owner's calendars with their ids and time zones.",
                {"type": "object", "properties": {}},
                self._wrap(self.list_calendars),
            ),
            Tool(
                "list_events",
                "List events on one or more whole days, in the calendar's own time zone.",
                {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "First day, YYYY-MM-DD."},
                        "days": {"type": "integer", "minimum": 1, "maximum": 31, "default": 1},
                        "calendar_id": {"type": "string", "default": "primary"},
                        "query": {"type": "string", "description": "Optional text to match."},
                    },
                    "required": ["start_date"],
                },
                self._wrap(self.list_events),
            ),
            Tool(
                "get_event",
                "Get one event's full details by id.",
                {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "calendar_id": {"type": "string", "default": "primary"},
                    },
                    "required": ["event_id"],
                },
                self._wrap(self.get_event),
            ),
        ]

    @staticmethod
    def _wrap(method: Callable[..., Awaitable[str]]) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def run(arguments: dict[str, Any]) -> str:
            return await method(**arguments)

        return run

    async def list_calendars(self) -> str:
        data = await self._fetch("/users/me/calendarList", {"minAccessRole": "reader"})
        lines = [
            f"{c.get('summary', '?')} (id {c['id']}, {c.get('timeZone', '?')})"
            + (" [primary]" if c.get("primary") else "")
            for c in data.get("items", [])
        ]
        return "\n".join(lines) or "No calendars."

    async def list_events(
        self, start_date: str, days: int = 1, calendar_id: str = "primary", query: str = ""
    ) -> str:
        first = date.fromisoformat(start_date)
        days = max(1, min(int(days), 31))
        zone = ZoneInfo(await self._time_zone(calendar_id))
        start = datetime.combine(first, time.min, zone)
        end = datetime.combine(first + timedelta(days=days), time.min, zone)
        params = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": MAX_EVENTS,
            "timeZone": zone.key,
        }
        if query:
            params["q"] = query
        data = await self._fetch(f"/calendars/{_q(calendar_id)}/events", params)
        events = [e for e in data.get("items", []) if e.get("status") != "cancelled"]
        if not events:
            span = start_date if days == 1 else f"{days} days from {start_date}"
            return f"No events on {span} ({zone.key})."
        return f"Times in {zone.key}.\n" + "\n".join(_summary(e, zone) for e in events)

    async def get_event(self, event_id: str, calendar_id: str = "primary") -> str:
        data = await self._fetch(f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", {})
        keep = ("summary", "start", "end", "location", "description", "attendees", "organizer", "hangoutLink")
        return json.dumps({k: data[k] for k in keep if k in data}, ensure_ascii=False)

    async def _time_zone(self, calendar_id: str) -> str:
        # calendarList, not calendars.get: the read-only scopes Abogi asks for
        # (calendarlist.readonly, events.readonly) do not cover calendars.get.
        if calendar_id not in self._zones:
            data = await self._fetch(f"/users/me/calendarList/{_q(calendar_id)}", {})
            self._zones[calendar_id] = data.get("timeZone", "UTC")
        return self._zones[calendar_id]


def _q(segment: str) -> str:
    """Encode a model-supplied id as one URL path segment."""
    return quote(str(segment), safe="")


def _summary(event: dict[str, Any], zone: ZoneInfo) -> str:
    start, end = event.get("start", {}), event.get("end", {})
    if "date" in start:
        when = f"{start['date']} all day"
    else:
        begin = datetime.fromisoformat(start["dateTime"]).astimezone(zone)
        finish = datetime.fromisoformat(end["dateTime"]).astimezone(zone)
        when = f"{begin:%a %d %b %H:%M}-{finish:%H:%M}"
    where = f" at {event['location']}" if event.get("location") else ""
    return f"{when}: {event.get('summary', '(no title)')}{where} (id {event['id']})"
