"""Profile and history: what Abogi keeps about the owner between conversations.

- The **profile** is a short Markdown note of facts and preferences ("I take
  calls after 10 AM", "Sam is my manager"). It is added to the instructions
  every turn, cached for a few minutes. The owner reads and edits it with
  `python -m abogi.profile show|add|edit`; Abogi adds to it with `remember`.
- The **history** is a log of the outward steps Abogi ran after approval:
  when, the read-back the owner approved, and the outcome. The core records
  it (not the model), and `recent_actions` reads it back.

Both live outside the repository: as Key Vault secrets (`abogi-profile`,
`abogi-history`) in `ABOGI_PROFILE_VAULT` (default: the Google token's
vault), or as files under ~/.config/abogi without a vault. The hosted agent
needs "Key Vault Secrets Officer" on that vault to write; with read access
only, the profile still works and writes report that they need it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from abogi.core.loop import Tool

PROFILE = "abogi-profile"
HISTORY = "abogi-history"
CACHE_SECONDS = 300.0
MAX_PROFILE_CHARS = 6000
KEEP_ACTIONS = 200


class Store(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


class FileStore:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def get(self, name: str) -> str | None:
        path = self._folder / f"{name}.txt"
        return path.read_text() if path.exists() else None

    def set(self, name: str, value: str) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._folder / f"{name}.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(value)


class VaultStore:
    def __init__(self, vault_url: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        self._client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    def get(self, name: str) -> str | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._client.get_secret(name).value
        except ResourceNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        self._client.set_secret(name, value, content_type="text/markdown")


def build_store(env: Mapping[str, str] | None = None) -> Store:
    env = os.environ if env is None else env
    vault = (env.get("ABOGI_PROFILE_VAULT") or env.get("ABOGI_GOOGLE_TOKEN_VAULT") or "").strip()
    if vault:
        return VaultStore(vault)
    return FileStore(Path(env.get("ABOGI_PROFILE_DIR") or Path.home() / ".config" / "abogi"))


class Memory:
    """Profile and history over one store, safe to share across turns."""

    def __init__(self, store: Store, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._clock = clock
        self._cached: tuple[float, str] | None = None

    # --- profile ---------------------------------------------------------------

    async def profile(self) -> str:
        if self._cached and self._clock() - self._cached[0] < CACHE_SECONDS:
            return self._cached[1]
        try:
            text = (await asyncio.to_thread(self._store.get, PROFILE)) or ""
        except Exception:  # noqa: BLE001 - no profile is better than no answer
            text = self._cached[1] if self._cached else ""
        self._cached = (self._clock(), text[:MAX_PROFILE_CHARS])
        return self._cached[1]

    async def remember(self, fact: str) -> str:
        fact = " ".join(fact.split())
        if not fact:
            return "Nothing to remember."
        try:
            current = (await asyncio.to_thread(self._store.get, PROFILE)) or ""
            updated = (current.rstrip() + "\n" if current.strip() else "") + f"- {fact}\n"
            if len(updated) > MAX_PROFILE_CHARS:
                return "Not saved: the profile is full. Ask the owner to tidy it (abogi profile edit)."
            await asyncio.to_thread(self._store.set, PROFILE, updated)
        except Exception as error:  # noqa: BLE001
            return f"Not saved ({_why(error)}). The owner can add it with: abogi profile add"
        self._cached = (self._clock(), updated)
        return f"Remembered: {fact}"

    # --- history ---------------------------------------------------------------

    async def record(self, read_back: str, outcome: str, when: datetime) -> None:
        """Log one approved step. Failure is swallowed: the step already ran."""
        try:
            entries = self._entries(await asyncio.to_thread(self._store.get, HISTORY))
            entries.append({"at": when.isoformat(timespec="minutes"), "step": read_back, "outcome": outcome[:300]})
            await asyncio.to_thread(self._store.set, HISTORY, json.dumps(entries[-KEEP_ACTIONS:]))
        except Exception:  # noqa: BLE001
            pass

    async def recent(self, limit: int = 10) -> str:
        try:
            entries = self._entries(await asyncio.to_thread(self._store.get, HISTORY))
        except Exception as error:  # noqa: BLE001
            return f"History is unavailable ({_why(error)})."
        if not entries:
            return "No approved actions recorded yet."
        return "\n".join(f"{e['at']}: {e['step']} -> {e['outcome']}" for e in entries[-max(1, limit):])

    @staticmethod
    def _entries(raw: str | None) -> list[dict[str, Any]]:
        try:
            data = json.loads(raw or "[]")
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def tools(self) -> list[Tool]:
        async def remember(arguments: dict[str, Any]) -> str:
            return await self.remember(str(arguments.get("fact", "")))

        async def recent(arguments: dict[str, Any]) -> str:
            return await self.recent(int(arguments.get("limit") or 10))

        return [
            Tool(
                "remember",
                "Save a lasting fact or preference about the owner to their profile, when they ask "
                "you to remember something or state a clear standing preference.",
                {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
                remember,
            ),
            Tool(
                "recent_actions",
                "List the outward steps you ran for the owner after approval, newest last.",
                {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
                recent,
            ),
        ]


def with_profile(instructions: str, profile: str) -> str:
    if not profile.strip():
        return instructions
    return (
        f"{instructions}\n\nAbout the owner (their profile, written by them or saved at their "
        f"request; use it, do not read it out):\n{profile.strip()}"
    )


def _why(error: Exception) -> str:
    text = str(error)
    if "Forbidden" in text or "403" in text or "not authorized" in text.lower():
        return "no permission to write the profile store; it needs Key Vault Secrets Officer"
    return f"{type(error).__name__}: {text[:120]}"


# --- owner command: python -m abogi.profile ---------------------------------------


def main(argv: list[str] | None = None) -> None:
    import argparse
    import subprocess
    import tempfile

    parser = argparse.ArgumentParser(prog="python -m abogi.profile", description="Read or edit Abogi's profile.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print the profile")
    add = sub.add_parser("add", help="add one fact")
    add.add_argument("fact", nargs="+")
    sub.add_parser("edit", help="edit the profile in $EDITOR")
    sub.add_parser("history", help="print the approved actions")
    args = parser.parse_args(argv)

    store = build_store()
    memory = Memory(store)
    if args.command == "show":
        print(store.get(PROFILE) or "(empty profile)")
    elif args.command == "add":
        print(asyncio.run(memory.remember(" ".join(args.fact))))
    elif args.command == "history":
        print(asyncio.run(memory.recent(50)))
    else:
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as handle:
            handle.write(store.get(PROFILE) or "# About me\n")
            path = handle.name
        subprocess.run([os.environ.get("EDITOR", "nano"), path], check=False)
        text = Path(path).read_text()
        os.unlink(path)
        if len(text) > MAX_PROFILE_CHARS:
            sys.exit(f"Not saved: keep the profile under {MAX_PROFILE_CHARS} characters.")
        store.set(PROFILE, text)
        print("Profile saved.")


if __name__ == "__main__":
    main()
