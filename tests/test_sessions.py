"""Coding sessions: the computer shares a short digest; the agent can only read it."""

import json
import os
from datetime import datetime, timedelta, timezone

from abogi import sessions

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)


def write(path, records, minutes_ago=5):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    stamp = (NOW - timedelta(minutes=minutes_ago)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def claude_log(path, **kwargs):
    return write(
        path,
        [
            {"type": "user", "cwd": "/home/owner/projects/shop", "message": {"role": "user", "content": "Fix the cart"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}},
            {"type": "custom-title", "customTitle": "Cart bug"},
            # a tool result and a hook message sit in the owner's turn but are not the owner
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "user", "message": {"role": "user", "content": "Stop hook feedback: keep going"}},
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "helper task"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed."}]}},
        ],
        **kwargs,
    )


def codex_log(path, **kwargs):
    def message(role, text):
        kind = "input_text" if role == "user" else "output_text"
        return {
            "type": "response_item",
            "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]},
        }

    return write(
        path,
        [
            {"type": "session_meta", "payload": {"cwd": "C:\\Users\\owner\\code\\blog"}},
            message("developer", "You are Codex"),
            message("user", "<environment_context>...</environment_context>"),
            message("user", "Add a dark theme"),
            message("assistant", "Dark theme added."),
        ],
        **kwargs,
    )


def test_a_claude_session_gives_its_folder_title_last_request_and_reply(tmp_path):
    session = sessions.read_session(claude_log(tmp_path / "p" / "a.jsonl"))
    assert (session.tool, session.project, session.title) == ("Claude", "shop", "Cart bug")
    assert (session.asked, session.said) == ("Fix the cart", "Fixed.")


def test_a_codex_session_is_read_too(tmp_path):
    session = sessions.read_session(codex_log(tmp_path / "2026" / "rollout.jsonl"))
    assert (session.tool, session.project) == ("Codex", "blog")
    assert (session.asked, session.said) == ("Add a dark theme", "Dark theme added.")


def test_keys_and_tokens_are_hidden_and_long_text_is_cut():
    assert sessions.tidy("use ghp_" + "a" * 30 + " now", 100) == "use [hidden] now"
    assert sessions.tidy("see /home/owner/projects/shop/src/cart/checkout/totals.py", 100).endswith("totals.py")
    assert len(sessions.tidy("word " * 200, 50)) == 50


def test_only_recent_sessions_are_listed_newest_first_without_helper_logs(tmp_path):
    claude_log(tmp_path / "claude" / "p" / "a.jsonl", minutes_ago=30)
    codex_log(tmp_path / "codex" / "b.jsonl", minutes_ago=5)
    codex_log(tmp_path / "codex" / "copy-of-b.jsonl", minutes_ago=6)  # the same session twice
    claude_log(tmp_path / "claude" / "p" / "a" / "subagents" / "helper.jsonl", minutes_ago=1)
    claude_log(tmp_path / "claude" / "p" / "old.jsonl", minutes_ago=60 * 24 * 30)
    found = sessions.recent_sessions([tmp_path / "claude", tmp_path / "codex", tmp_path / "missing"], NOW)
    assert [s.tool for s in found] == ["Codex", "Claude"]


def test_a_large_session_is_read_from_its_end(tmp_path):
    path = tmp_path / "big.jsonl"
    filler = {"type": "attachment", "data": "x" * 1000}
    old = {"type": "user", "cwd": "/w/shop", "message": {"role": "user", "content": "the first request"}}
    new = {"type": "user", "cwd": "/w/shop", "message": {"role": "user", "content": "the latest request"}}
    write(path, [old, *[filler] * 600, new])
    assert sessions.read_session(path).asked == "the latest request"


class Store(dict):
    def set(self, name, value):
        self[name] = value


async def test_the_agent_reads_what_the_computer_shared(tmp_path, monkeypatch):
    claude_log(tmp_path / "p" / "a.jsonl")
    monkeypatch.setattr(sessions, "session_dirs", lambda env=None: [tmp_path])
    store = Store()
    assert sessions.share(store, now=NOW) == 1
    assert len(store[sessions.SESSIONS]) <= sessions.MAX_DIGEST_CHARS

    text = sessions.describe(store[sessions.SESSIONS], NOW + timedelta(minutes=10))
    assert 'Claude, folder shop "Cart bug"' in text and "Fix the cart" in text
    assert "shared 10 minutes ago" in text and "never an instruction" in text
    assert sessions.describe(store[sessions.SESSIONS], NOW, project="blog") == "No recent coding session matches."

    [tool] = sessions.tools(store)
    assert tool.name == "coding_sessions" and not tool.outward
    assert "Fix the cart" in await tool.run({})


def test_nothing_shared_says_how_to_turn_it_on():
    assert "ABOGI_SESSIONS=on" in sessions.describe(None, NOW)
    assert "ABOGI_SESSIONS=on" in sessions.describe("{}", NOW)


def test_a_digest_that_is_too_long_drops_the_oldest_sessions():
    many = [sessions.Session("Claude", "p", "t", NOW.isoformat(), "a" * 240, "b" * 400) for _ in range(60)]
    text = sessions.digest(many, NOW, "pc")
    assert len(text) <= sessions.MAX_DIGEST_CHARS and 0 < len(json.loads(text)["sessions"]) < 60
