"""Windows tray voice client: hold a key to talk to Abogi (step 2).

    uv run --extra tray --env-file .env python -m abogi.channels.tray

Hold the push-to-talk key (default F9, or ABOGI_VOICE_KEY) and speak; release
to send. Press again while Abogi talks to interrupt. A bar pops up at the
bottom of the screen while a turn is going on, and the tray icon shows the
state: grey idle, blue warming, red listening, amber thinking, green speaking,
black offline. Right-click the icon to quit.

Runs on Windows, not WSL: WSL sees neither global keys nor the tray.

Sign-in: a device-code sign-in on first run (DeviceCodeCredential), then
silent from the Windows token cache; set ABOGI_TENANT_ID to your Entra
tenant. Voice Live needs `Cognitive Services User` and `Foundry User` on the
Foundry resource. One Voice Live connection is kept open; if it drops, the
client reconnects to the same agent conversation (`conversation_id`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from abogi.channels.voice import (
    DEFAULT_VOICE,
    IDLE,
    LISTENING,
    SAMPLE_RATE,
    SPEAKING,
    THINKING,
    WARMING,
    VoiceSession,
    session_config,
)

logger = logging.getLogger("abogi.tray")

CHUNK_FRAMES = SAMPLE_RATE // 20  # 50 ms of mic audio per append
OFFLINE = "offline"
COLOURS = {
    IDLE: (128, 128, 128),
    WARMING: (60, 120, 220),
    LISTENING: (220, 40, 40),
    THINKING: (235, 170, 20),
    SPEAKING: (40, 170, 80),
    OFFLINE: (20, 20, 20),
}
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "abogi"


# --- sign-in ---------------------------------------------------------------


class AsyncCredential:
    """Wrap a sync azure-identity credential for the async Voice Live client."""

    def __init__(self, credential: Any) -> None:
        self._credential = credential

    async def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._credential.get_token, *scopes, **kwargs)

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> AsyncCredential:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass


def sign_in(tenant_id: str | None) -> AsyncCredential:
    """Device-code sign-in once; afterwards silent from the persistent token cache.

    Device code, not a browser redirect: the owner types a short code at
    microsoft.com/devicelogin, so no localhost redirect has to reach this process.
    """
    from azure.identity import (
        AuthenticationRecord,
        DeviceCodeCredential,
        TokenCachePersistenceOptions,
    )

    def show_code(verification_uri: str, user_code: str, _expires: Any) -> None:
        print(f"\nTo sign in to Azure, open {verification_uri} and enter the code {user_code}\n", flush=True)

    record_file = STATE_DIR / "azure-auth-record.json"
    record = None
    if record_file.exists():
        record = AuthenticationRecord.deserialize(record_file.read_text())
    credential = DeviceCodeCredential(
        tenant_id=tenant_id,
        cache_persistence_options=TokenCachePersistenceOptions(name="abogi"),
        authentication_record=record,
        prompt_callback=show_code,
        timeout=900,
    )
    if record is None:
        record = credential.authenticate(scopes=["https://ai.azure.com/.default"])
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        record_file.write_text(record.serialize())  # account ids only, no tokens
    return AsyncCredential(credential)


# --- audio -----------------------------------------------------------------


class Speaker:
    """Plays queued pcm16 from a sounddevice callback; `flush()` drops what is queued."""

    def __init__(self) -> None:
        import sounddevice

        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream = sounddevice.RawOutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_FRAMES,
            callback=self._fill,
        )
        self._stream.start()

    def _fill(self, out: Any, frames: int, _time: Any, _status: Any) -> None:
        wanted = frames * 2
        with self._lock:
            chunk = bytes(self._buffer[:wanted])
            del self._buffer[:wanted]
        out[:] = chunk.ljust(wanted, b"\x00")

    def play(self, pcm: bytes) -> None:
        with self._lock:
            self._buffer.extend(pcm)

    def flush(self) -> None:
        with self._lock:
            self._buffer.clear()

    def close(self) -> None:
        self._stream.close()


def open_microphone(on_chunk: Callable[[bytes], None]) -> Any:
    import sounddevice

    def capture(data: Any, _frames: int, _time: Any, _status: Any) -> None:
        on_chunk(bytes(data))

    stream = sounddevice.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_FRAMES,
        callback=capture,
    )
    stream.start()
    return stream


# --- tray icon ---------------------------------------------------------------


def icon_image(state: str) -> Any:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((6, 6, 58, 58), fill=COLOURS.get(state, COLOURS[OFFLINE]))
    return image


# --- pop-up status bar -------------------------------------------------------------

LABELS = {
    WARMING: "Warming up…",
    LISTENING: "Listening… release to send",
    THINKING: "Thinking…",
    SPEAKING: "Speaking — press to interrupt",
    OFFLINE: "Offline — reconnecting",
    IDLE: "Ready",
}


class Overlay:
    """A small always-on-top bar near the bottom of the screen showing the state.

    Tk must own the main thread, so other threads call `show()`, which only
    queues the change; the Tk loop applies it.
    """

    def __init__(self) -> None:
        import queue
        import tkinter

        self._queue: queue.SimpleQueue[tuple[str, str | None, int | None]] = queue.SimpleQueue()
        self.root = tkinter.Tk()
        self.root.withdraw()
        self._bar = tkinter.Toplevel(self.root)
        self._bar.overrideredirect(True)  # no title bar or taskbar button
        self._bar.attributes("-topmost", True)
        self._bar.attributes("-alpha", 0.93)
        self._label = tkinter.Label(
            self._bar, font=("Segoe UI", 13, "bold"), fg="white", padx=22, pady=10
        )
        self._label.pack()
        self._bar.withdraw()
        self._hide_job: str | None = None
        self.root.after(50, self._poll)

    def show(self, state: str, text: str | None = None, linger_ms: int | None = None) -> None:
        self._queue.put((state, text, linger_ms))

    def stop(self) -> None:
        self._queue.put(("__quit__", None, None))

    def run(self) -> None:
        self.root.mainloop()

    def _poll(self) -> None:
        while not self._queue.empty():
            state, text, linger_ms = self._queue.get()
            if state == "__quit__":
                self.root.quit()
                return
            self._apply(state, text, linger_ms)
        self.root.after(50, self._poll)

    def _apply(self, state: str, text: str | None, linger_ms: int | None) -> None:
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if state == IDLE and text is None:
            self._bar.withdraw()  # back to idle after a turn: get out of the way
            return
        red, green, blue = COLOURS.get(state, COLOURS[OFFLINE])
        colour = f"#{red:02x}{green:02x}{blue:02x}"
        self._label.configure(text=f"Abogi · {text or LABELS.get(state, state)}", bg=colour)
        self._bar.configure(bg=colour)
        self._bar.update_idletasks()
        width = self._bar.winfo_reqwidth()
        x = (self._bar.winfo_screenwidth() - width) // 2
        y = self._bar.winfo_screenheight() - self._bar.winfo_reqheight() - 90
        self._bar.geometry(f"+{x}+{y}")
        self._bar.deiconify()
        self._bar.lift()
        if linger_ms:
            self._hide_job = self.root.after(linger_ms, self._bar.withdraw)


# --- the client ----------------------------------------------------------------


class TrayClient:
    def __init__(self, endpoint: str, agent: str, key_name: str, voice: str, tenant_id: str | None):
        from urllib.parse import urlsplit

        parts = urlsplit(endpoint)
        self._resource = f"{parts.scheme}://{parts.netloc}"
        self._project = parts.path.rstrip("/").rsplit("/", 1)[-1]
        self._agent = agent
        self._key_name = key_name.lower()
        self._voice = voice
        self._tenant_id = tenant_id
        self._loop = asyncio.new_event_loop()
        self._session: VoiceSession | None = None
        self._icon: Any = None
        self._overlay: Overlay | None = None
        self._announce_ready = False
        self._stopping = False

    # Called from other threads (keyboard hook, audio callback, tray): hop onto the loop.
    def _submit(self, make: Callable[[VoiceSession], Any]) -> None:
        session = self._session
        if session is not None and not self._stopping:
            asyncio.run_coroutine_threadsafe(make(session), self._loop)

    def _show(self, state: str, text: str | None = None, linger_ms: int | None = None) -> None:
        if state == IDLE and text is None and self._announce_ready:
            self._announce_ready = False
            logger.info("Ready. Hold %s to talk.", self._key_name.upper())
            text, linger_ms = f"Ready — hold {self._key_name.upper()} to talk", 4000
        if self._icon is not None:
            self._icon.icon = icon_image(state)
            self._icon.title = f"Abogi: {state}"
        if self._overlay is not None:
            self._overlay.show(state, text, linger_ms)
        logger.debug("state: %s", state)

    def _said(self, who: str, text: str) -> None:
        if text:
            logger.info("%s: %s", who, text)

    async def _run(self, credential: AsyncCredential, speaker: Speaker) -> None:
        from azure.ai.voicelive.aio import connect

        conversation_id: str | None = None
        delay = 1.0
        while not self._stopping:
            try:
                async with connect(
                    endpoint=self._resource,
                    credential=credential,
                    agent_name=self._agent,
                    project_name=self._project,
                    conversation_id=conversation_id,
                ) as connection:
                    await connection.session.update(session=session_config(self._voice))
                    session = VoiceSession(
                        connection, speaker, on_state=self._show, on_text=self._said
                    )
                    session.conversation_id = conversation_id
                    self._session = session
                    logger.info("Connected to Voice Live (agent %s); warming up.", self._agent)
                    self._announce_ready = True  # say "Ready" once the warm-up is done
                    self._show(WARMING)
                    await session.warm()
                    delay = 1.0
                    rewarm = asyncio.create_task(self._keep_warm(session))
                    try:
                        async for event in connection:
                            await session.handle(event)
                            conversation_id = session.conversation_id
                    finally:
                        rewarm.cancel()
            except Exception as error:  # noqa: BLE001 - reconnect on any connection failure
                logger.warning("Voice Live connection lost: %s: %s", type(error).__name__, error)
            self._session = None
            self._show(OFFLINE)
            if not self._stopping:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _keep_warm(self, session: VoiceSession) -> None:
        while True:
            await asyncio.sleep(30)
            if session.state == IDLE:
                await session.warm()

    def run(self) -> None:
        import pystray
        from pynput import keyboard

        credential = sign_in(self._tenant_id)
        speaker = Speaker()
        microphone = open_microphone(lambda pcm: self._submit(lambda s: s.audio(pcm)))

        def matches(key: Any) -> bool:
            name = getattr(key, "name", None) or getattr(key, "char", None) or ""
            return str(name).lower() == self._key_name

        held = threading.Event()

        def on_press(key: Any) -> None:
            if matches(key) and not held.is_set():
                held.set()
                self._submit(lambda s: s.key_down())

        def on_release(key: Any) -> None:
            if matches(key):
                held.clear()
                self._submit(lambda s: s.key_up())

        self._overlay = Overlay()
        self._show(WARMING, "Connecting…")

        def quit_app(icon: Any, _item: Any) -> None:
            self._stopping = True
            icon.stop()
            self._overlay.stop()

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        loop_thread = threading.Thread(
            target=self._loop.run_until_complete,
            args=(self._run(credential, speaker),),
            name="abogi-voice",
            daemon=True,
        )
        loop_thread.start()
        self._icon = pystray.Icon(
            "abogi",
            icon_image(OFFLINE),
            "Abogi: connecting",
            menu=pystray.Menu(pystray.MenuItem("Quit Abogi", quit_app)),
        )
        self._icon.run_detached()  # the tray runs on its own thread; Tk needs the main one
        try:
            self._overlay.run()  # blocks on the main thread until Quit (or Ctrl-C)
        except KeyboardInterrupt:
            pass
        finally:
            self._icon.stop()
            self._stopping = True
            listener.stop()
            microphone.close()
            speaker.close()


def main() -> None:
    logging.basicConfig(level=os.environ.get("ABOGI_LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    TrayClient(
        endpoint=os.environ["ABOGI_PROJECT_ENDPOINT"],
        agent=os.environ.get("ABOGI_AGENT_NAME", "abogi"),
        key_name=os.environ.get("ABOGI_VOICE_KEY", "f9"),
        voice=os.environ.get("ABOGI_VOICE", DEFAULT_VOICE),
        tenant_id=os.environ.get("ABOGI_TENANT_ID") or None,
    ).run()


if __name__ == "__main__":
    main()
