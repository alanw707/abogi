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


def test_reading_is_free_and_every_change_is_outward():
    fetch, _ = fake_api()
    tools = anyio.run(GoogleCalendarConnector(fetch).tools)
    assert [t.name for t in tools if not t.outward] == ["list_calendars", "list_events", "get_event"]
    assert [t.name for t in tools if t.outward] == [
        "create_event", "move_event", "cancel_event", "respond_to_event"
    ]
    assert all(t.read_back for t in tools if t.outward)


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
    assert "Thu 19 Nov 9:00 AM-9:15 AM: Standup at Zoom (id a)" in answer
    assert "2026-11-19 all day: Holiday (id b)" in answer
    assert "Gone" not in answer


def test_spoken_clock_is_twelve_hour():
    from datetime import datetime

    from abogi.connectors.google_calendar import spoken_clock

    assert spoken_clock(datetime(2026, 11, 19, 14, 35)) == "2:35 PM"
    assert spoken_clock(datetime(2026, 11, 19, 0, 5)) == "12:05 AM"
    assert spoken_clock(datetime(2026, 11, 19, 12, 0)) == "12:00 PM"


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


def test_the_calendar_time_zone_is_looked_up_once():
    fetch, calls = fake_api()
    connector = GoogleCalendarConnector(fetch)
    run(connector, "list_events", start_date="2026-11-19")
    run(connector, "list_events", start_date="2026-11-20")
    lookups = [path for path, _ in calls if path.startswith("/users/me/calendarList/")]
    assert lookups == ["/users/me/calendarList/primary"]


# --- changing events ----------------------------------------------------------


EVENT = {
    "id": "ev1",
    "summary": "Dentist",
    "start": {"dateTime": "2026-11-23T14:00:00-08:00"},
    "end": {"dateTime": "2026-11-23T14:30:00-08:00"},
    "attendees": [{"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
                  {"email": "sam@example.com"}],
}


def writable_api():
    calls = []

    async def fetch(path, params, *, method="GET", body=None):
        calls.append((method, path, params, body))
        if path.startswith("/users/me/calendarList/"):
            return {"timeZone": "America/Los_Angeles"}
        if method == "DELETE":
            return {}
        if method in ("POST", "PATCH"):
            return {**EVENT, **(body or {}), "id": "ev1"}
        return dict(EVENT, attendees=[dict(a) for a in EVENT["attendees"]])

    return fetch, calls


def tool(connector, name):
    return {t.name: t for t in anyio.run(connector.tools)}[name]


def test_create_reads_back_in_spoken_time_and_sends_local_times():
    fetch, calls = writable_api()
    create = tool(GoogleCalendarConnector(fetch), "create_event")
    args = {"title": "Lunch", "start": "2026-11-24T12:00", "end": "2026-11-24T13:00", "guests": ["sam@example.com"]}
    said = anyio.run(create.read_back, args)
    assert said == 'Create "Lunch" on Tue 24 Nov, 12:00 PM to 1:00 PM and invite sam@example.com.'
    assert all(method == "GET" for method, *_ in calls)  # reading back changes nothing

    anyio.run(create.run, args)
    method, path, params, body = calls[-1]
    assert (method, path, params) == ("POST", "/calendars/primary/events", {"sendUpdates": "all"})
    assert body["start"] == {"dateTime": "2026-11-24T12:00:00-08:00", "timeZone": "America/Los_Angeles"}
    assert body["attendees"] == [{"email": "sam@example.com"}]


def test_all_day_events_end_the_next_day():
    fetch, calls = writable_api()
    anyio.run(tool(GoogleCalendarConnector(fetch), "create_event").run,
              {"title": "Off", "start": "2026-11-27", "end": "2026-11-27"})
    body = calls[-1][3]
    assert body["start"] == {"date": "2026-11-27"} and body["end"] == {"date": "2026-11-28"}


def test_move_reads_back_the_event_by_name_and_tells_guests():
    fetch, calls = writable_api()
    move = tool(GoogleCalendarConnector(fetch), "move_event")
    args = {"event_id": "ev1", "start": "2026-11-23T15:00", "end": "2026-11-23T15:30"}
    said = anyio.run(move.read_back, args)
    assert said == ('Move "Dentist" from on Mon 23 Nov, 2:00 PM to 2:30 PM to on Mon 23 Nov, '
                    "3:00 PM to 3:30 PM, and tell its 1 guest.")
    anyio.run(move.run, args)
    assert calls[-1][:3] == ("PATCH", "/calendars/primary/events/ev1", {"sendUpdates": "all"})


def test_cancel_deletes_and_tells_guests():
    fetch, calls = writable_api()
    result = anyio.run(tool(GoogleCalendarConnector(fetch), "cancel_event").run, {"event_id": "ev1"})
    assert calls[-1][:3] == ("DELETE", "/calendars/primary/events/ev1", {"sendUpdates": "all"})
    assert result.startswith("Cancelled: Mon 23 Nov 2:00 PM-2:30 PM: Dentist")


def test_responding_changes_only_the_owners_answer():
    fetch, calls = writable_api()
    anyio.run(tool(GoogleCalendarConnector(fetch), "respond_to_event").run, {"event_id": "ev1", "response": "accepted"})
    attendees = next(body for method, _, _, body in calls if method == "PATCH")["attendees"]
    assert attendees[0]["responseStatus"] == "accepted"
    assert "responseStatus" not in attendees[1]


def test_an_end_before_the_start_is_refused():
    fetch, _ = writable_api()
    with pytest.raises(ValueError, match="after the start"):
        anyio.run(tool(GoogleCalendarConnector(fetch), "create_event").run,
                  {"title": "x", "start": "2026-11-24T13:00", "end": "2026-11-24T12:00"})
