"""Wake word: nothing streams until "Hey Jarvis", then one question streams."""

import pytest

from abogi.channels.voice import IDLE, LISTENING, VoiceSession

from test_voice import Clock, FakeConnection, FakePlayer, audio, created, done, warmed, speech


def wake_session():
    connection, player, clock = FakeConnection(), FakePlayer(), Clock()
    session = VoiceSession(connection, player, clock=clock, hands_free=True, wake_word=True)
    return session, connection, player, clock


async def test_nothing_is_streamed_before_the_wake_word():
    session, connection, _, clock = wake_session()
    await warmed(session, clock)
    await session.audio(b"private room talk")
    assert connection.sent == []


async def test_waking_sends_the_last_moment_then_streams_until_the_turn_ends():
    session, connection, player, clock = wake_session()
    await warmed(session, clock)
    await session.audio(b"hey jarvis")
    await session.wake()
    assert session.state == LISTENING
    assert connection.names() == ["append"]  # the pre-roll
    await session.audio(b"what is next")
    await session.handle(speech("started"))
    await session.handle(speech("stopped"))
    await session.audio(b"after the question")
    assert connection.names() == ["append", "append"]  # asleep again once the turn ended
    await session.handle(created("r1"))
    await session.handle(audio("r1", b"ok"))
    await session.handle(done("r1"))
    assert bytes(player.played) == b"ok" and session.state == IDLE


async def test_a_false_wake_goes_back_to_sleep():
    session, connection, _, clock = wake_session()
    await warmed(session, clock)
    await session.wake()
    clock.now += 9
    await session.audio(b"silence")
    assert session.state == IDLE
    await session.audio(b"later talk")
    assert connection.names() == []


async def test_the_wake_word_interrupts_an_answer():
    session, connection, player, clock = wake_session()
    await warmed(session, clock)
    await session.wake()
    await session.handle(speech("started"))
    await session.handle(speech("stopped"))
    await session.handle(created("r1"))
    await session.handle(audio("r1", b"long answer"))
    await session.wake()
    assert ("response.cancel", {"response_id": "r1"}) in connection.sent
    assert player.flushes >= 1 and session.state == LISTENING


# --- the detector -------------------------------------------------------------


import numpy as np  # noqa: E402 - a dev dependency; the tray gets it with openwakeword


class FakeModel:
    def __init__(self, scores):
        self.scores = list(scores)
        self.frames = []

    def predict(self, frame):
        self.frames.append(frame)
        return {"hey_jarvis_v0.1": self.scores.pop(0) if self.scores else 0.0}


def test_detector_scores_80ms_frames_at_16khz_and_fires_once_per_phrase():
    from abogi.channels.wake import WakeDetector

    woke, now = [], [0.0]
    model = FakeModel([0.1, 0.9, 0.95, 0.2])
    detector = WakeDetector(lambda: woke.append(1), model=model, clock=lambda: now[0])
    chunk = np.zeros(1200, dtype=np.int16).tobytes()  # 50 ms at 24 kHz
    for _ in range(16):  # 800 ms
        detector.process(chunk)
    assert len(model.frames) == 10  # 800 ms / 80 ms
    assert all(len(frame) == 1280 for frame in model.frames)
    assert woke == [1]  # 0.9 fired; 0.95 right after is the same phrase


def test_the_wake_word_is_built_in_or_a_custom_model_named_by_its_file():
    from abogi.channels.wake import WakeWord, wake_word

    assert wake_word(None) == WakeWord("hey_jarvis", "hey_jarvis", "Hey Jarvis")
    custom = wake_word(r"C:\Users\owner\abogi\hey_abboggy.onnx")
    assert custom.key == "hey_abboggy" and custom.phrase == "Hey Abboggy"
    assert custom.source.endswith("hey_abboggy.onnx")


def test_a_custom_model_is_scored_by_its_own_key():
    from abogi.channels.wake import WakeDetector

    class Custom(FakeModel):
        def predict(self, frame):
            return {"hey_abboggy": self.scores.pop(0) if self.scores else 0.0}

    woke = []
    detector = WakeDetector(lambda: woke.append(1), model=Custom([0.8]), name="hey_abboggy")
    detector.process(np.zeros(1920, dtype=np.int16).tobytes())  # one 80 ms frame
    assert woke == [1]


def test_a_missing_custom_model_says_where_it_looked(tmp_path):
    from abogi.channels.wake import load_model

    with pytest.raises(FileNotFoundError, match="No wake word model"):
        load_model(str(tmp_path / "hey_abboggy.onnx"))


def test_downsampling_keeps_two_of_every_three_samples():
    from abogi.channels.wake import downsample_24k_to_16k

    out = downsample_24k_to_16k(np.array([30, 60, 90, 300, 600, 900], dtype=np.int16))
    assert out.tolist() == [30, 75, 300, 750]
