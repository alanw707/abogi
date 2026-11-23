"""Briefing times, the alert queue, and speaking first (the tray client's proactive logic)."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from test_voice import Clock, FakeConnection, FakePlayer, audio, created, done, warmed

from abogi.channels.proactive import AlertQueue, Briefings, alert_prompt, parse_alerts, parse_times
from abogi.channels.voice import IDLE, VoiceSession

LA = ZoneInfo("America/Los_Angeles")


def at(hour, minute, day=23):
    return datetime(2026, 11, day, hour, minute, tzinfo=LA)


def test_times_parse_and_bad_ones_are_ignored():
    assert parse_times("17:45, 07:30, soon") == [time(7, 30), time(17, 45)]
    assert parse_times("") == []


def test_a_briefing_fires_once_a_day_within_its_grace():
    briefings = Briefings([time(7, 30)])
    assert not briefings.due(at(7, 29))
    assert briefings.due(at(7, 31))
    assert not briefings.due(at(7, 45))  # already given today
    assert briefings.due(at(7, 35, day=24))  # and again tomorrow


def test_starting_late_does_not_give_the_morning_briefing():
    assert not Briefings([time(7, 30)]).due(at(12, 0))


def test_each_alert_is_said_once():
    queue = AlertQueue()
    alert = {"id": "event:e1", "text": "Dentist starts at 2:00 PM, in 8 minutes."}
    assert queue.add([alert]) == 1
    assert queue.add([alert]) == 0  # the next check returns it again
    assert queue.pop() == alert["text"]
    assert queue.pop() is None


def test_an_unreadable_answer_is_no_alerts():
    assert parse_alerts("not json") == []
    assert parse_alerts('{"alerts": [{"id": "a", "text": "b"}, 3]}') == [{"id": "a", "text": "b"}]
    assert alert_prompt("Soon.").startswith("(Abogi alert)")


def session_parts():
    connection, player, clock = FakeConnection(), FakePlayer(), Clock()
    return VoiceSession(connection, player, clock=clock), connection, player, clock


async def test_announce_speaks_first_only_when_idle():
    session, connection, player, clock = session_parts()
    await warmed(session, clock)
    assert await session.announce(alert_prompt("Dentist soon.")) is True
    assert connection.names() == ["item.create", "response.create"]
    assert connection.sent[0][1]["item"].content[0].text.startswith("(Abogi alert)")
    await session.handle(created("a1"))
    await session.handle(audio("a1", b"soon"))
    assert bytes(player.played) == b"soon"
    assert await session.announce("again") is False  # busy speaking: wait
    await session.handle(done("a1"))
    assert session.state == IDLE


async def test_the_owner_can_interrupt_an_announcement():
    session, connection, _, clock = session_parts()
    await warmed(session, clock)
    await session.announce("(Abogi briefing) Give me my briefing.")
    await session.handle(created("b1"))
    await session.key_down()
    assert ("response.cancel", {"response_id": "b1"}) in connection.sent
