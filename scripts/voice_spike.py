"""Spike (step 2, issue 2): Voice Live in front of the hosted agent.

Sends typed questions as text turns over one Voice Live connection in agent
mode and reports, per turn, the time to the first transcript text, the first
audio and the end of the response, plus the spoken transcript. Optionally
saves the reply audio as WAV. No microphone needed, so it runs in WSL.

Usage:
  uv run --extra voice --env-file .env python scripts/voice_spike.py \
      [--voice en-US-Ava:DragonHDLatestNeural] [--wav-dir DIR] "question" ["follow-up" ...]

Needs ABOGI_PROJECT_ENDPOINT (the Voice Live endpoint is the resource part of
it) and an Azure sign-in with Foundry User on the project. Agent mode takes
no `instructions`; the persona comes from the agent itself (ADR 0002).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import InputTextContentPart, UserMessageItem
from azure.identity.aio import DefaultAzureCredential

from abogi.channels.warm import WARM_UP

SAMPLE_RATE = 24000  # pcm16 mono, the service default


def split_project_endpoint(endpoint: str) -> tuple[str, str]:
    """https://<res>.services.ai.azure.com/api/projects/<p> -> (resource URL, project)."""
    parts = urlsplit(endpoint)
    project = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return f"{parts.scheme}://{parts.netloc}", project


async def ask(connection, question: str, wav: Path | None, *, silent: bool = False) -> dict:
    # A typed item: the SDK turns a plain dict into the base item class, which has no `role`.
    await connection.conversation.item.create(
        item=UserMessageItem(content=[InputTextContentPart(text=question)])
    )
    started = time.perf_counter()
    # silent: a text-only reply, so a warm-up turn is never spoken aloud.
    await connection.response.create(response={"modalities": ["text"]} if silent else None)
    marks: dict[str, float] = {}
    transcript: list[str] = []
    audio = bytearray()
    ids: dict[str, str] = {}

    def mark(name: str) -> None:
        marks.setdefault(name, time.perf_counter() - started)

    async for event in connection:
        kind = getattr(event, "type", "")
        kind = getattr(kind, "value", kind)
        if kind == "response.created":
            response = getattr(event, "response", None)
            for key in ("id", "conversation_id"):
                value = getattr(response, key, None)
                if value:
                    ids[key] = value
        elif kind in ("response.audio_transcript.delta", "response.text.delta"):
            mark("first text")
            transcript.append(event.delta)
        elif kind == "response.audio.delta":
            mark("first audio")
            audio.extend(event.delta if isinstance(event.delta, bytes) else base64.b64decode(event.delta))
        elif kind == "response.done":
            mark("done")
            status = getattr(getattr(event, "response", None), "status", None)
            ids["status"] = str(getattr(status, "value", status))
            break
        elif kind == "error":
            mark("done")
            ids["error"] = json.dumps(event.as_dict().get("error", {}))[:300]
            break

    if wav and audio:
        with wave.open(str(wav), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(bytes(audio))
    return {
        "marks": marks,
        "text": "".join(transcript),
        "audio_seconds": len(audio) / (2 * SAMPLE_RATE),
        **ids,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("questions", nargs="+")
    parser.add_argument("--agent", default="abogi")
    parser.add_argument("--voice", default="en-US-Ava:DragonHDLatestNeural")
    parser.add_argument("--wav-dir", type=Path)
    parser.add_argument("--warm", action="store_true", help="send a silent warm-up turn first")
    args = parser.parse_args()

    resource, project = split_project_endpoint(os.environ["ABOGI_PROJECT_ENDPOINT"])
    async with DefaultAzureCredential() as credential:
        started = time.perf_counter()
        async with connect(
            endpoint=resource, credential=credential, agent_name=args.agent, project_name=project,
            credential_scopes=os.environ.get("ABOGI_VOICE_SCOPE") or None,
        ) as connection:
            await connection.session.update(
                session={
                    "modalities": ["text", "audio"],
                    "voice": {"name": args.voice, "type": "azure-standard"},
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": None,  # push-to-talk: the client says when a turn ends
                }
            )
            async for event in connection:
                kind = getattr(getattr(event, "type", ""), "value", getattr(event, "type", ""))
                if kind == "session.updated":
                    session = event.as_dict().get("session", {})
                    keep = {k: session.get(k) for k in ("id", "voice", "turn_detection", "agent")}
                    print(f"connected in {time.perf_counter() - started:.1f}s: {json.dumps(keep)[:400]}")
                    break
                if kind == "error":
                    raise SystemExit(f"session.update failed: {event.as_dict()}")

            if args.warm:
                result = await ask(connection, WARM_UP, None, silent=True)
                marks = "  ".join(f"{k} {v:.1f}s" for k, v in result["marks"].items())
                print(f"\n[warm-up] {marks}  audio {result['audio_seconds']:.1f}s  said: {result['text']}")

            for n, question in enumerate(args.questions, 1):
                wav = args.wav_dir / f"turn{n}.wav" if args.wav_dir else None
                result = await ask(connection, question, wav)
                marks = "  ".join(f"{k} {v:.1f}s" for k, v in result.pop("marks").items())
                print(f"\n[{n}] {question}\n    {marks}  audio {result.pop('audio_seconds'):.1f}s")
                print(f"    said: {result.pop('text')}")
                if result:
                    print(f"    {result}")


if __name__ == "__main__":
    asyncio.run(main())
