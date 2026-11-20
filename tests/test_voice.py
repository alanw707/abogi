import base64
from types import SimpleNamespace as NS

import pytest

from abogi.channels.voice import (
    IDLE,
    LISTENING,
    SPEAKING,
    THINKING,
    WARMING,
    VoiceSession,
    session_config,
)
from abogi.channels.warm import WARM_UP


class FakeConnection:
    """Records client events as (name, kwargs)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        rec = self._recorder
        self.response = NS(create=rec("response.create"), cancel=rec("response.cancel"))
        self.input_audio_buffer = NS(
            append=rec("append"), commit=rec("commit"), clear=rec("clear")
        )
        self.conversation = NS(item=NS(create=rec("item.create")))

    def _recorder(self, name):
        async def record(**kwargs):
            self.sent.append((name, kwargs))

        return record

    def names(self) -> list[str]:
        return [name for name, _ in self.sent]


class FakePlayer:
    def __init__(self) -> None:
        self.played = bytearray()
        self.flushes = 0

    def play(self, pcm: bytes) -> None:
        self.played.extend(pcm)

    def flush(self) -> None:
        self.flushes += 1


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def created(rid, conversation="conv_1"):
    return NS(type="response.created", response=NS(id=rid, conversation_id=conversation))


def audio(rid, pcm=b"\x01\x02"):
    return NS(type="response.audio.delta", response_id=rid, delta=base64.b64encode(pcm).decode())


def done(rid):
    return NS(type="response.done", response=NS(id=rid))


@pytest.fixture
def parts():
    connection, player, clock, states = FakeConnection(), FakePlayer(), Clock(), []
    session = VoiceSession(connection, player, clock=clock, on_state=states.append)
    return session, connection, player, clock, states


async def warmed(session, clock):
    await session.warm()
    await session.handle(created("warm"))
    await session.handle(done("warm"))
    session._connection.sent.clear()


def test_session_config_is_push_to_talk_without_instructions():
    config = session_config()
    assert config["turn_detection"] is None
    assert "instructions" not in config


async def test_warm_up_is_silent_text_and_returns_to_idle(parts):
    session, connection, _, _, states = parts
    assert await session.warm() is True
    item = connection.sent[0][1]["item"]
    assert item.content[0].text == WARM_UP
    assert connection.sent[1] == ("response.create", {"response": {"modalities": ["text"]}})
    await session.handle(created("warm"))
    await session.handle(done("warm"))
    assert states == [WARMING, IDLE]
    assert session.conversation_id == "conv_1"
    assert await session.warm() is False  # warm now


async def test_a_turn_streams_mic_audio_then_plays_the_reply(parts):
    session, connection, player, clock, states = parts
    await warmed(session, clock)
    await session.key_down()
    await session.key_down()  # key repeat is ignored
    await session.audio(b"mic")
    clock.now += 1
    await session.key_up()
    assert connection.names() == ["clear", "append", "commit", "response.create"]
    assert connection.sent[1][1]["audio"] == base64.b64encode(b"mic").decode()

    await session.handle(created("r1"))
    await session.handle(audio("r1", b"hi"))
    await session.handle(done("r1"))
    assert bytes(player.played) == b"hi"
    assert states[-4:] == [LISTENING, THINKING, SPEAKING, IDLE]


async def test_mic_audio_is_not_sent_unless_the_key_is_held(parts):
    session, connection, _, clock, _ = parts
    await warmed(session, clock)
    await session.audio(b"noise")
    assert connection.sent == []


async def test_a_short_tap_is_thrown_away(parts):
    session, connection, _, clock, _ = parts
    await warmed(session, clock)
    await session.key_down()
    clock.now += 0.1
    await session.key_up()
    assert connection.names() == ["clear", "clear"]
    assert session.state == IDLE


async def test_pressing_while_speaking_cancels_and_ignores_late_audio(parts):
    session, connection, player, clock, _ = parts
    await warmed(session, clock)
    await session.key_down()
    clock.now += 1
    await session.key_up()
    await session.handle(created("r1"))
    await session.handle(audio("r1", b"a"))

    await session.key_down()  # barge in
    assert player.flushes == 1
    assert ("response.cancel", {"response_id": "r1"}) in connection.sent
    await session.handle(audio("r1", b"late"))
    await session.handle(done("r1"))
    assert bytes(player.played) == b"a"
    assert session.state == LISTENING


async def test_pressing_before_the_reply_is_created_cancels_it_once_created(parts):
    session, connection, player, clock, _ = parts
    await warmed(session, clock)
    await session.key_down()
    clock.now += 1
    await session.key_up()
    await session.key_down()  # still thinking, no response id yet
    await session.handle(created("r1"))
    assert connection.sent[-1] == ("response.cancel", {"response_id": "r1"})
    await session.handle(audio("r1"))
    assert player.played == bytearray()


async def test_release_during_warm_up_waits_for_it_then_asks(parts):
    session, connection, player, clock, states = parts
    await session.key_down()  # cold: key-down starts the warm-up
    assert connection.names() == ["clear", "item.create", "response.create"]
    await session.handle(created("warm"))
    clock.now += 1
    await session.key_up()
    assert connection.names()[-1] == "commit"  # no second response.create yet

    await session.handle(done("warm"))
    assert connection.names()[-1] == "response.create"
    await session.handle(created("r1"))
    await session.handle(audio("r1", b"x"))
    await session.handle(done("r1"))
    assert bytes(player.played) == b"x"
    assert session.state == IDLE


async def test_goes_cold_after_idle_and_rewarms(parts):
    session, connection, _, clock, _ = parts
    await warmed(session, clock)
    clock.now += 900
    assert session.is_cold()
    assert await session.warm() is True


async def test_an_error_returns_to_idle(parts):
    session, _, _, clock, _ = parts
    await warmed(session, clock)
    await session.key_down()
    clock.now += 1
    await session.key_up()
    await session.handle(NS(type="error", error=NS(message="boom")))
    assert session.state == IDLE
