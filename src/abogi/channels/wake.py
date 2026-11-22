"""Wake word: hear "Hey Jarvis" on this computer before anything is streamed (step 6).

`WakeDetector` takes the microphone's 24 kHz pcm16 chunks, turns them into
the 16 kHz, 80 ms frames openWakeWord expects, and calls `on_wake` when the
model's score crosses `threshold`. Detection runs on its own thread, so the
audio callback only queues bytes.

Nothing leaves the computer until the wake word is heard: the voice session
sends audio only while awake (see `VoiceSession.wake()`).

The model is openWakeWord's pre-trained "hey_jarvis" (Apache 2.0), downloaded
once on first use, unless `ABOGI_WAKE_WORD` names another: a built-in model
name, or the path of a custom .onnx model trained with openWakeWord's notebook
(such as hey_abboggy.onnx). openWakeWord needs onnxruntime, which has no wheels
for Python 3.15 yet, so the tray runs on Python 3.14.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "hey_jarvis"
FRAME = 1280  # samples at 16 kHz: 80 ms, openWakeWord's frame
COOLDOWN_SECONDS = 2.0  # one phrase fires once


class WakeWord(NamedTuple):
    source: str  # what openWakeWord loads: a built-in name or a .onnx path
    key: str  # prefix of the model's score key (the file name for a custom model)
    phrase: str  # what the owner says, for the tray


def wake_word(setting: str | None) -> WakeWord:
    """The wake word from ABOGI_WAKE_WORD: a built-in name, or a custom .onnx file."""
    source = (setting or "").strip() or DEFAULT_MODEL
    # PureWindowsPath splits on / and \, so a Windows path works wherever this runs
    key = PureWindowsPath(source).stem if source.lower().endswith(".onnx") else source
    return WakeWord(source, key, key.replace("_", " ").title())


class WakeDetector:
    def __init__(
        self,
        on_wake: Callable[[], None],
        *,
        model: Any = None,
        name: str = DEFAULT_MODEL,
        source: str | None = None,
        threshold: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_wake = on_wake
        self._model = model  # anything with predict(int16 frame) -> {name: score}
        self._name = name
        self._source = source or name
        self._threshold = threshold
        self._clock = clock
        self._pending = bytearray()  # 24 kHz bytes not yet resampled
        self._frame: list[int] = []  # 16 kHz samples not yet scored
        self._last_wake = -COOLDOWN_SECONDS
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
        self._thread: threading.Thread | None = None

    # --- threads -----------------------------------------------------------

    def start(self) -> None:
        if self._model is None:
            self._model = load_model(self._source)
        self._thread = threading.Thread(target=self._work, name="abogi-wake", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)

    def feed(self, pcm24k: bytes) -> None:
        """Called from the audio callback: queue and return at once."""
        try:
            self._queue.put_nowait(pcm24k)
        except queue.Full:
            pass  # detection fell behind; dropping audio is better than lag

    def _work(self) -> None:
        while (chunk := self._queue.get()) is not None:
            try:
                self.process(chunk)
            except Exception as error:  # noqa: BLE001 - keep listening
                logger.warning("Wake word detection failed: %s: %s", type(error).__name__, error)

    # --- detection (called on the worker thread, or directly by tests) -------

    def process(self, pcm24k: bytes) -> bool:
        """Score whole frames in this chunk; True if the wake word fired."""
        import numpy as np

        self._pending.extend(pcm24k)
        usable = len(self._pending) // 6 * 6  # 3 samples of 2 bytes -> 2 samples at 16 kHz
        if usable:
            samples = np.frombuffer(bytes(self._pending[:usable]), dtype=np.int16)
            del self._pending[:usable]
            self._frame.extend(downsample_24k_to_16k(samples).tolist())
        fired = False
        while len(self._frame) >= FRAME:
            frame = np.asarray(self._frame[:FRAME], dtype=np.int16)
            del self._frame[:FRAME]
            scores = self._model.predict(frame)
            # Keys are model names, possibly with a version ("hey_jarvis_v0.1").
            score = max((float(v) for k, v in scores.items() if k.startswith(self._name)), default=0.0)
            now = self._clock()
            if score >= self._threshold and now - self._last_wake >= COOLDOWN_SECONDS:
                self._last_wake = now
                logger.info("Wake word heard (score %.2f).", score)
                self._on_wake()
                fired = True
        return fired


def downsample_24k_to_16k(samples: Any) -> Any:
    """24 kHz -> 16 kHz: every 3 input samples give 2 (at 0 and 1.5 of them).

    Crude next to a polyphase filter, but the wake model is robust to it and
    it avoids pulling in scipy's resampler for a 2:3 ratio.
    """
    import numpy as np

    x = samples.astype(np.float32).reshape(-1, 3)
    out = np.empty((x.shape[0], 2), dtype=np.float32)
    out[:, 0] = x[:, 0]
    out[:, 1] = (x[:, 1] + x[:, 2]) / 2
    return np.clip(out.reshape(-1), -32768, 32767).astype(np.int16)


def load_model(name: str) -> Any:
    """openWakeWord's model for `name` (downloaded the first time), or a custom .onnx file."""
    if name.lower().endswith(".onnx"):
        path = Path(name).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"No wake word model at {path}")
        from openwakeword.model import Model

        return Model(wakeword_models=[str(path)], inference_framework="onnx")
    import openwakeword
    from openwakeword.model import Model

    try:
        return Model(wakeword_models=[name], inference_framework="onnx")
    except Exception:  # noqa: BLE001 - most likely not downloaded yet
        logger.info("Downloading the %s wake word model (once).", name)
        openwakeword.utils.download_models(model_names=[name])
        return Model(wakeword_models=[name], inference_framework="onnx")
