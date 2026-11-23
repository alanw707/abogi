"""Wake word: nothing streams until "Hey Jarvis", then one question streams."""

import pytest
from test_voice import Clock, FakeConnection, FakePlayer, audio, created, done, speech, warmed

from abogi.channels.voice import IDLE, LISTENING, THINKING, VoiceSession


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
    assert bytes(player.played) == b"ok"


ONE_SECOND = b"\x00\x00" * 24000  # pcm16 at 24 kHz


async def answered(session, clock, reply=ONE_SECOND):
    """Wake, ask one question, and get a reply of `reply`'s length."""
    await warmed(session, clock)
    await session.wake()
    await session.handle(speech("started"))
    await session.handle(speech("stopped"))
    await session.handle(created("r1"))
    await session.handle(audio("r1", reply))
    await session.handle(done("r1"))
    session._connection.sent.clear()


async def test_after_an_answer_a_follow_up_needs_no_wake_word():
    session, connection, _, clock = wake_session()
    await answered(session, clock)
    assert session.state == LISTENING
    await session.audio(b"abogi's own voice")  # the answer is still playing
    assert connection.names() == []
    clock.now += 1.5
    await session.audio(b"and tomorrow?")
    assert connection.names() == ["append"]
    await session.handle(speech("started"))
    await session.handle(speech("stopped"))
    assert session.state == THINKING


async def test_an_unanswered_follow_up_goes_back_to_sleep():
    session, connection, _, clock = wake_session()
    await answered(session, clock)
    clock.now += 1 + 9  # the answer, then more than the follow-up window
    await session.audio(b"room talk")
    assert session.state == IDLE and connection.names() == []


async def test_a_noise_that_gets_no_reply_does_not_leave_it_thinking():
    session, connection, _, clock = wake_session()
    await answered(session, clock)
    clock.now += 1.5
    await session.handle(speech("started"))  # a cough in the follow-up window
    await session.handle(speech("stopped"))
    assert session.state == THINKING
    clock.now += 5
    await session.audio(b"quiet")
    assert session.state == THINKING  # a real reply may still be starting
    clock.now += 6
    await session.audio(b"quiet")
    assert session.state == IDLE and connection.names() == []
    await session.wake()  # and the wake word still works
    assert session.state == LISTENING


def test_the_bar_says_how_to_send_and_interrupt_in_each_mode():
    from abogi.channels.tray import label
    from abogi.channels.voice import SPEAKING

    assert label(LISTENING) == "Listening… release to send"
    assert "pause" in label(LISTENING, wake_phrase="Hey Abboggy")
    assert label(SPEAKING, wake_phrase="Hey Abboggy") == 'Speaking — say "Hey Abboggy" or press F9 to interrupt'
    assert label(SPEAKING, hands_free=True) == "Speaking — talk or press F9 to interrupt"


def test_the_tray_says_when_it_stops_listening():
    from abogi.channels.tray import fell_asleep
    from abogi.channels.voice import SPEAKING, WARMING

    assert fell_asleep(LISTENING, IDLE, wake=True)  # the follow-up window ran out
    assert fell_asleep(SPEAKING, IDLE, wake=True)  # F9 stopped the answer
    assert not fell_asleep(WARMING, IDLE, wake=True)  # start-up, not a conversation ending
    assert not fell_asleep(IDLE, IDLE, wake=True)
    assert not fell_asleep(LISTENING, IDLE, wake=False)  # push-to-talk: nothing was listening


def test_the_sleep_chime_is_short_and_soft():
    from abogi.channels.tray import sleep_chime
    from abogi.channels.voice import SAMPLE_RATE

    pcm = np.frombuffer(sleep_chime(), dtype=np.int16)
    assert 0.2 < len(pcm) / SAMPLE_RATE < 0.5
    assert np.abs(pcm).max() < 32767 * 0.3


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


def test_a_custom_model_on_a_fresh_install_downloads_the_shared_feature_models(tmp_path, monkeypatch):
    import sys
    import types

    from abogi.channels.wake import load_model

    custom = tmp_path / "hey_abboggy.onnx"
    custom.write_bytes(b"onnx")
    downloaded: list[list[str]] = []

    class Model:
        def __init__(self, wakeword_models, inference_framework):
            # openWakeWord's melspectrogram and embedding models come with any download
            if not downloaded:
                raise RuntimeError("melspectrogram.onnx failed: File doesn't exist")
            self.models = wakeword_models

    package = types.ModuleType("openwakeword")
    package.utils = types.SimpleNamespace(download_models=lambda model_names: downloaded.append(model_names))
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = Model
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)

    assert load_model(str(custom)).models == [str(custom)]
    assert len(downloaded) == 1


def test_downsampling_keeps_two_of_every_three_samples():
    from abogi.channels.wake import downsample_24k_to_16k

    out = downsample_24k_to_16k(np.array([30, 60, 90, 300, 600, 900], dtype=np.int16))
    assert out.tolist() == [30, 75, 300, 750]


def test_the_tray_keeps_its_log_in_a_file_without_a_line_per_request(tmp_path):
    import logging

    from abogi.channels.tray import log_to_file

    root = logging.Logger("test-root")
    log_to_file(root, tmp_path / "abogi" / "tray.log")
    root.handle(logging.LogRecord("httpx", logging.INFO, "", 0, "HTTP Request: POST /responses", None, None))
    root.handle(logging.LogRecord("httpx", logging.WARNING, "", 0, "request failed", None, None))
    root.handle(logging.LogRecord("abogi", logging.INFO, "", 0, "Wake word heard (score 0.90).", None, None))
    for handler in root.handlers:
        handler.close()
    text = (tmp_path / "abogi" / "tray.log").read_text()
    assert "Wake word heard" in text and "request failed" in text and "HTTP Request" not in text
