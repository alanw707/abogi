"""Google Calendar through Google's regular Calendar API (v3).

Google's remote Calendar MCP server needs the Cloud project enrolled in the
Workspace Developer Preview Program, so this connector calls the GA REST API
directly instead (ADR 0003). It offers the same kind of read tools, built for
a spoken assistant: dates instead of RFC 3339 ranges, and short plain-text
results.

Changing events is outward (guests are told), so those tools are marked
`outward` and only run after the owner's approval (ADR 0004). Each one reads
back the event by title and time, looked up from Google, not by id.

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

# fetch(path, params, *, method="GET", body=None) -> the JSON reply ({} when empty)
Fetch = Callable[..., Awaitable[dict[str, Any]]]


def http_fetch(
    token: TokenProvider, timeout: float = 20.0, *, base: str = API, service: str = "Google Calendar"
) -> Fetch:
    """Call a Google API path (under `base`) with the owner's bearer token."""

    async def fetch(
        path: str, params: dict[str, Any], *, method: str = "GET", body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {await token()}"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method, f"{base}{path}", params=params, headers=headers, json=body
            )
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:200]
            raise RuntimeError(f"{service} said {response.status_code}: {message}")
        return response.json() if response.content else {}

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
            Tool(
                "create_event",
                "Create an event on the owner's calendar, optionally inviting guests. "
                "Needs the owner's approval.",
                {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": WHEN},
                        "end": {"type": "string", "description": WHEN},
                        "guests": {"type": "array", "items": {"type": "string"}, "description": "Email addresses."},
                        "location": {"type": "string"},
                        "description": {"type": "string"},
                        "calendar_id": {"type": "string", "default": "primary"},
                    },
                    "required": ["title", "start", "end"],
                },
                self._wrap(self.create_event),
                outward=True,
                read_back=self._kw(self.read_back_create),
            ),
            Tool(
                "move_event",
                "Move an event to a new start and end. Guests are told. Needs the owner's approval.",
                {
                    "type": "object",
                    "properties": {
                        "event_id": EVENT_ID,
                        "start": {"type": "string", "description": WHEN},
                        "end": {"type": "string", "description": WHEN},
                        "calendar_id": {"type": "string", "default": "primary"},
                    },
                    "required": ["event_id", "start", "end"],
                },
                self._wrap(self.move_event),
                outward=True,
                read_back=self._kw(self.read_back_move),
            ),
            Tool(
                "cancel_event",
                "Delete an event. Guests are told. Needs the owner's approval.",
                {
                    "type": "object",
                    "properties": {"event_id": EVENT_ID, "calendar_id": {"type": "string", "default": "primary"}},
                    "required": ["event_id"],
                },
                self._wrap(self.cancel_event),
                outward=True,
                read_back=self._kw(self.read_back_cancel),
            ),
            Tool(
                "respond_to_event",
                "Accept, decline or tentatively accept an invitation. The organiser is told. "
                "Needs the owner's approval.",
                {
                    "type": "object",
                    "properties": {
                        "event_id": EVENT_ID,
                        "response": {"type": "string", "enum": ["accepted", "declined", "tentative"]},
                        "calendar_id": {"type": "string", "default": "primary"},
                    },
                    "required": ["event_id", "response"],
                },
                self._wrap(self.respond_to_event),
                outward=True,
                read_back=self._kw(self.read_back_respond),
            ),
        ]

    @staticmethod
    def _kw(method: Callable[..., Awaitable[str]]) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def read_back(arguments: dict[str, Any]) -> str:
            return await method(**arguments)

        return read_back

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

    # --- changing events (outward: held for approval) -------------------------

    async def create_event(
        self,
        title: str,
        start: str,
        end: str,
        guests: list[str] | None = None,
        location: str = "",
        description: str = "",
        calendar_id: str = "primary",
    ) -> str:
        zone = await self._time_zone(calendar_id)
        body: dict[str, Any] = {"summary": title, **_span(start, end, zone)}
        if guests:
            body["attendees"] = [{"email": guest} for guest in guests]
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        created = await self._fetch(
            f"/calendars/{_q(calendar_id)}/events",
            {"sendUpdates": "all" if guests else "none"},
            method="POST",
            body=body,
        )
        return f"Created: {_summary(created, ZoneInfo(zone))}"

    async def move_event(self, event_id: str, start: str, end: str, calendar_id: str = "primary") -> str:
        zone = await self._time_zone(calendar_id)
        moved = await self._fetch(
            f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}",
            {"sendUpdates": "all"},
            method="PATCH",
            body=_span(start, end, zone),
        )
        return f"Moved: {_summary(moved, ZoneInfo(zone))}"

    async def cancel_event(self, event_id: str, calendar_id: str = "primary") -> str:
        before = await self._event_line(event_id, calendar_id)
        await self._fetch(
            f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", {"sendUpdates": "all"}, method="DELETE"
        )
        return f"Cancelled: {before}"

    async def respond_to_event(self, event_id: str, response: str, calendar_id: str = "primary") -> str:
        if response not in RESPONSES:
            raise ValueError(f"response must be one of {', '.join(RESPONSES)}")
        path = f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}"
        event = await self._fetch(path, {})
        attendees = event.get("attendees") or []
        if not any(a.get("self") for a in attendees):
            raise ValueError("The owner is not a guest of this event, so there is nothing to answer.")
        for attendee in attendees:
            if attendee.get("self"):
                attendee["responseStatus"] = response
        await self._fetch(path, {"sendUpdates": "all"}, method="PATCH", body={"attendees": attendees})
        return f"Answered {RESPONSES[response]}: {_summary(event, ZoneInfo(await self._time_zone(calendar_id)))}"

    async def read_back_create(
        self, title: str, start: str, end: str, guests: list[str] | None = None, calendar_id: str = "primary", **_: Any
    ) -> str:
        zone = ZoneInfo(await self._time_zone(calendar_id))
        when = _spoken_span(_span(start, end, zone.key), zone)
        invite = f" and invite {_and(guests)}" if guests else ""
        return f"Create \"{title}\" {when}{invite}."

    async def read_back_move(self, event_id: str, start: str, end: str, calendar_id: str = "primary", **_: Any) -> str:
        zone = ZoneInfo(await self._time_zone(calendar_id))
        event = await self._fetch(f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", {})
        return (
            f"Move \"{event.get('summary', '(no title)')}\" from {_spoken_span(event, zone)} "
            f"to {_spoken_span(_span(start, end, zone.key), zone)}{_guests_told(event)}."
        )

    async def read_back_cancel(self, event_id: str, calendar_id: str = "primary", **_: Any) -> str:
        zone = ZoneInfo(await self._time_zone(calendar_id))
        event = await self._fetch(f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", {})
        return f"Cancel \"{event.get('summary', '(no title)')}\" {_spoken_span(event, zone)}{_guests_told(event)}."

    async def read_back_respond(self, event_id: str, response: str, calendar_id: str = "primary", **_: Any) -> str:
        zone = ZoneInfo(await self._time_zone(calendar_id))
        event = await self._fetch(f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", {})
        verb = {"accepted": "Accept", "declined": "Decline", "tentative": "Tentatively accept"}.get(response, response)
        return f"{verb} \"{event.get('summary', '(no title)')}\" {_spoken_span(event, zone)}."

    async def _event_line(self, event_id: str, calendar_id: str) -> str:
        zone = ZoneInfo(await self._time_zone(calendar_id))
        event = await self._fetch(f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", {})
        return _summary({**event, "id": event_id}, zone)

    async def _time_zone(self, calendar_id: str) -> str:
        # calendarList, not calendars.get: the scopes Abogi asks for
        # (calendarlist.readonly, events) do not cover calendars.get.
        if calendar_id not in self._zones:
            data = await self._fetch(f"/users/me/calendarList/{_q(calendar_id)}", {})
            self._zones[calendar_id] = data.get("timeZone", "UTC")
        return self._zones[calendar_id]


EVENT_ID = {"type": "string", "description": "The event id from list_events."}
WHEN = "Local time in the calendar's time zone, YYYY-MM-DDTHH:MM; or YYYY-MM-DD for all day."
RESPONSES = {"accepted": "yes", "declined": "no", "tentative": "maybe"}


def _span(start: str, end: str, zone: str) -> dict[str, Any]:
    """Event start and end in API form, from the model's local times or dates."""
    if len(start) == 10 and len(end) == 10:  # all day; the API's end date is exclusive
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        return {"start": {"date": first.isoformat()}, "end": {"date": max(last, first + timedelta(days=1)).isoformat()}}
    tz = ZoneInfo(zone)
    begin, finish = (_local(value, tz) for value in (start, end))
    if finish <= begin:
        raise ValueError("The end must be after the start.")
    return {
        "start": {"dateTime": begin.isoformat(), "timeZone": zone},
        "end": {"dateTime": finish.isoformat(), "timeZone": zone},
    }


def _local(value: str, zone: ZoneInfo) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)


def _spoken_span(event: dict[str, Any], zone: ZoneInfo) -> str:
    """ "on Mon 23 Nov, 2:00 PM to 2:30 PM" or "on Mon 23 Nov, all day"."""
    start, end = event.get("start", {}), event.get("end", {})
    if "date" in start:
        return f"on {date.fromisoformat(start['date']):%a %d %b}, all day"
    begin = datetime.fromisoformat(start["dateTime"]).astimezone(zone)
    finish = datetime.fromisoformat(end["dateTime"]).astimezone(zone)
    return f"on {begin:%a %d %b}, {spoken_clock(begin)} to {spoken_clock(finish)}"


def _guests_told(event: dict[str, Any]) -> str:
    others = [a for a in event.get("attendees") or [] if not a.get("self")]
    return f", and tell its {len(others)} guest{'s' if len(others) != 1 else ''}" if others else ""


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _q(segment: str) -> str:
    """Encode a model-supplied id as one URL path segment."""
    return quote(str(segment), safe="")


def spoken_clock(moment: datetime) -> str:
    """12-hour time as it is said aloud: "2:35 PM", "12:05 AM".

    The tool hands the model times in the form it should speak, so the model
    does not convert 24-hour times itself (it did so unevenly).
    """
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def _summary(event: dict[str, Any], zone: ZoneInfo) -> str:
    start, end = event.get("start", {}), event.get("end", {})
    if "date" in start:
        when = f"{start['date']} all day"
    else:
        begin = datetime.fromisoformat(start["dateTime"]).astimezone(zone)
        finish = datetime.fromisoformat(end["dateTime"]).astimezone(zone)
        when = f"{begin:%a %d %b} {spoken_clock(begin)}-{spoken_clock(finish)}"
    where = f" at {event['location']}" if event.get("location") else ""
    return f"{when}: {event.get('summary', '(no title)')}{where} (id {event['id']})"
