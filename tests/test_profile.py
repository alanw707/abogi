"""Profile and history: kept between conversations, outside the repository."""

import json
from datetime import datetime
from types import SimpleNamespace

import anyio
from starlette.testclient import TestClient

from azure.ai.agentserver.responses import InMemoryResponseProvider

from abogi.core.loop import Tool
from abogi.hosting.app import create_app
from abogi.profile import HISTORY, PROFILE, FileStore, Memory, with_profile


class ReadOnly:
    def __init__(self, profile=""):
        self.values = {PROFILE: profile}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        raise PermissionError("(Forbidden) Caller is not authorized to perform action on resource")


def test_remember_appends_to_the_profile_file(tmp_path):
    memory = Memory(FileStore(tmp_path))
    assert anyio.run(memory.remember, "Sam is my manager.") == "Remembered: Sam is my manager."
    anyio.run(memory.remember, "I take calls after 10 AM.")
    assert (tmp_path / f"{PROFILE}.txt").read_text() == "- Sam is my manager.\n- I take calls after 10 AM.\n"
    assert "Sam is my manager." in anyio.run(memory.profile)


def test_without_write_access_the_profile_is_read_and_saving_says_why():
    memory = Memory(ReadOnly("- Prefers mornings."))
    assert anyio.run(memory.profile) == "- Prefers mornings."
    said = anyio.run(memory.remember, "Likes tea.")
    assert said.startswith("Not saved (no permission") and "abogi profile add" in said


def test_history_keeps_the_newest_entries(tmp_path):
    memory = Memory(FileStore(tmp_path))
    when = datetime(2026, 11, 23, 9, 0)
    for n in range(205):
        anyio.run(memory.record, f"Step {n}.", "done", when)
    entries = json.loads((tmp_path / f"{HISTORY}.txt").read_text())
    assert len(entries) == 200 and entries[-1]["step"] == "Step 204."
    assert anyio.run(memory.recent, 1) == "2026-11-23T09:00: Step 204. -> done"


def test_the_profile_goes_into_the_instructions_marked_as_the_owners():
    assert with_profile("Be brief.", "") == "Be brief."
    text = with_profile("Be brief.", "- Sam is my manager.")
    assert text.startswith("Be brief.") and text.endswith("- Sam is my manager.")
    assert "do not read it out" in text


class Item(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return dict(vars(self))


class FakeModel:
    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.responses = self

    async def create(self, **request):
        self.requests.append(json.loads(json.dumps(request)))
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def text(content):
    return SimpleNamespace(output=[Item(type="message", role="assistant", content=content)], output_text=content)


def test_an_approved_step_is_recorded_and_the_profile_reaches_the_model(tmp_path):
    memory = Memory(FileStore(tmp_path))
    anyio.run(memory.remember, "Sam is my manager.")

    async def send(arguments):
        return "Sent."

    tool = Tool("send_mail", "Send.", {"type": "object", "properties": {}}, send,
                outward=True, read_back=lambda a: "Send the email to Sam.")
    call = SimpleNamespace(output=[Item(type="function_call", call_id="c1", name="send_mail", arguments="{}")],
                           output_text="")
    model = FakeModel(call, text("Send it? Say Abogi, approve."), text("Sent."))

    async def tools():
        return [tool]

    app = create_app(client_factory=lambda: model, model="m", tools_provider=tools, store=InMemoryResponseProvider(),
                     skills_provider=lambda c, m: [], hosted_tools=[], memory=memory)
    with TestClient(app) as client:
        first = client.post("/responses", json={"model": "x", "input": "Email Sam."}).json()["id"]
        client.post("/responses", json={"model": "x", "input": "Abogi, approve", "previous_response_id": first})
    assert "Sam is my manager." in model.requests[0]["instructions"]
    assert "Send the email to Sam. -> Sent." in anyio.run(memory.recent, 5)
    assert {t["name"] for t in model.requests[0]["tools"]} >= {"remember", "recent_actions"}
