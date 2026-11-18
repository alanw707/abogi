"""The direct Google Calendar connector, against a scripted fake API."""

import anyio
import pytest

from abogi.connectors.calendar import build_calendar_connector
from abogi.connectors.google_calendar import GoogleCalendarConnector


def fake_api(events=(), zone="America/Los_Angeles"):
    calls = []

    async def fetch(path, params):
        calls.append((path, params))
        if path.endswith("/events"):
            return {"items": list(events)}
        if "/events/" in path:
            return {"summary": "One event"}
        if path.startswith("/calendars/"):
            raise AssertionError("calendars.get needs a scope Abogi does not ask for")
        if path.startswith("/users/me/calendarList/"):
            return {"timeZone": zone}
        return {"items": [{"id": "me@example.com", "summary": "Me", "timeZone": zone, "primary": True}]}

    return fetch, calls


def run(connector, name, **arguments):
    async def go():
        tools = {t.name: t for t in await connector.tools()}
        return await tools[name].run(arguments)

    return anyio.run(go)


def test_only_read_tools_are_offered():
    fetch, _ = fake_api()
    names = [t.name for t in anyio.run(GoogleCalendarConnector(fetch).tools)]
    assert names == ["list_calendars", "list_events", "get_event"]


def test_a_day_is_bounded_in_the_calendars_time_zone():
    fetch, calls = fake_api()
    answer = run(GoogleCalendarConnector(fetch), "list_events", start_date="2026-11-19")
    path, params = calls[-1]
    assert path == "/calendars/primary/events"
    assert params["timeMin"] == "2026-11-19T00:00:00-08:00"
    assert params["timeMax"] == "2026-11-20T00:00:00-08:00"
    assert answer == "No events on 2026-11-19 (America/Los_Angeles)."


def test_events_are_summarised_in_local_time_and_cancelled_ones_dropped():
    events = [
        {"id": "a", "summary": "Standup", "start": {"dateTime": "2026-11-19T17:00:00Z"},
         "end": {"dateTime": "2026-11-19T17:15:00Z"}, "location": "Zoom"},
        {"id": "b", "summary": "Holiday", "start": {"date": "2026-11-19"}, "end": {"date": "2026-11-20"}},
        {"id": "c", "summary": "Gone", "status": "cancelled", "start": {"date": "2026-11-19"}},
    ]
    fetch, _ = fake_api(events)
    answer = run(GoogleCalendarConnector(fetch), "list_events", start_date="2026-11-19")
    assert "Thu 19 Nov 09:00-09:15: Standup at Zoom (id a)" in answer
    assert "2026-11-19 all day: Holiday (id b)" in answer
    assert "Gone" not in answer


def test_ids_from_the_model_cannot_change_the_api_path():
    fetch, calls = fake_api()
    run(GoogleCalendarConnector(fetch), "get_event", event_id="../../users/me", calendar_id="x@group.calendar.google.com")
    assert calls[-1][0] == "/calendars/x%40group.calendar.google.com/events/..%2F..%2Fusers%2Fme"


def test_direct_api_needs_a_sign_in(tmp_path):
    with pytest.raises(ValueError, match="sign-in"):
        build_calendar_connector({"ABOGI_CALENDAR_API": "google", "ABOGI_GOOGLE_TOKEN_FILE": str(tmp_path / "none.json")})


def test_only_one_calendar_source_may_be_set():
    with pytest.raises(ValueError, match="only one of"):
        build_calendar_connector({"ABOGI_CALENDAR_API": "google", "ABOGI_CALENDAR_MCP_URL": "http://x"})
