"""Alerts: meetings about to start and mail from chosen senders, answered by code."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import anyio
from azure.ai.agentserver.responses import InMemoryResponseProvider
from starlette.testclient import TestClient

from abogi.connectors.google_calendar import GoogleCalendarConnector
from abogi.connectors.google_mail import GmailConnector
from abogi.hosting.app import create_app
from abogi.skills.alerts import ALERT_CHECK, collect, is_alert_check, settings

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 23, 13, 52, tzinfo=LA)


def calendar(items):
    calls = []

    async def fetch(path, params, **_):
        calls.append((path, params))
        if path.startswith("/users/me/calendarList/"):
            return {"timeZone": "America/Los_Angeles"}
        return {"items": items}

    return GoogleCalendarConnector(fetch), calls


def test_a_meeting_about_to_start_is_an_alert_and_all_day_ones_are_not():
    connector, calls = calendar([
        {"id": "e1", "summary": "Dentist", "start": {"dateTime": "2026-11-23T14:00:00-08:00"}},
        {"id": "e2", "summary": "Holiday", "start": {"date": "2026-11-23"}},
        {"id": "e3", "summary": "Gone", "status": "cancelled", "start": {"dateTime": "2026-11-23T14:00:00-08:00"}},
    ])
    alerts = anyio.run(connector.upcoming_alerts, NOW, 10)
    assert alerts == [{"id": "event:e1:2026-11-23T14:00:00-08:00", "text": "Dentist starts at 2:00 PM, in 8 minutes."}]
    assert calls[-1][1]["timeMax"] == "2026-11-23T14:02:00-08:00"


def test_mail_from_a_chosen_sender_is_an_alert():
    calls = []

    async def fetch(path, params, **_):
        calls.append((path, params))
        if path == "/messages":
            return {"messages": [{"id": "m9"}]}
        return {"payload": {"headers": [
            {"name": "From", "value": "Boss <boss@example.com>"},
            {"name": "Subject", "value": "Call me"},
        ]}}

    alerts = anyio.run(GmailConnector(fetch).mail_alerts, ["boss@example.com"])
    assert alerts == [{"id": "mail:m9", "text": "New email from Boss: Call me."}]
    assert calls[0][1]["q"] == "is:unread newer_than:1d from:(boss@example.com)"


def test_one_failing_source_does_not_hide_the_others():
    class Broken:
        async def upcoming_alerts(self, now, minutes):
            raise RuntimeError("google down")

    class Fine:
        async def upcoming_alerts(self, now, minutes):
            return [{"id": "x", "text": "Soon."}]

    assert anyio.run(collect, [Broken(), Fine()], {}, NOW) == [{"id": "x", "text": "Soon."}]


def test_settings_have_safe_defaults():
    assert settings({}) == (10, [])
    assert settings({"ABOGI_ALERT_MINUTES": "nope", "ABOGI_ALERT_FROM": "a@x.com, b@y.com"}) == (10, ["a@x.com", "b@y.com"])


def test_only_the_exact_marker_is_an_alert_check():
    assert is_alert_check([{"role": "user", "content": ALERT_CHECK}])
    assert not is_alert_check([{"role": "user", "content": f"what is {ALERT_CHECK}?"}])


def test_an_alert_check_is_answered_without_the_model():
    class NoModel:
        @property
        def responses(self):
            raise AssertionError("an alert check must not call the model")

    async def tools():
        return []

    async def alerts(now):
        return [{"id": "event:e1", "text": "Dentist starts at 2:00 PM, in 8 minutes."}]

    app = create_app(client_factory=NoModel, model="m", tools_provider=tools,
                     store=InMemoryResponseProvider(), alerts_provider=alerts)
    with TestClient(app) as client:
        reply = client.post("/responses", json={"model": "x", "input": ALERT_CHECK}).json()
    text = reply["output"][0]["content"][0]["text"]
    assert json.loads(text) == {"alerts": [{"id": "event:e1", "text": "Dentist starts at 2:00 PM, in 8 minutes."}]}
