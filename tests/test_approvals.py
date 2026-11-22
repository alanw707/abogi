"""The approval gate: outward steps run only after "Abogi, approve" (ADR 0004)."""

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from azure.ai.agentserver.responses import InMemoryResponseProvider

from abogi.core.approvals import Approvals, is_approval
from abogi.core.loop import Tool, ToolCall, run_turn
from abogi.hosting.app import create_app


@pytest.mark.parametrize(
    "text",
    ["Abogi, approve", "Abogi, approve.", "a bogey approve", "Approve.", "Abogi approved", "ABOGI, APPROVE!"],
)
def test_approval_phrase_survives_speech_recognition(text):
    assert is_approval(text)


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "sure, go ahead",
        "don't approve",
        "Abogi, do not approve",
        "approve the meeting with Sam tomorrow at noon",  # too long: a new request, not an approval
        "approve it later maybe",
        "",
    ],
)
def test_other_turns_are_not_approvals(text):
    assert not is_approval(text)


def test_a_pending_step_is_taken_once_and_expires():
    now = [0.0]
    book = Approvals(clock=lambda: now[0], ttl=600)
    call = ToolCall("c1", "send", "{}")
    book.hold("conv", call, "Send it.")
    assert book.take("conv").call == call
    assert book.take("conv") is None
    book.hold("conv", call, "Send it.")
    now[0] = 601
    assert book.take("conv") is None


# --- the loop holds outward calls -------------------------------------------


class Item(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return dict(vars(self))


def text(content):
    return SimpleNamespace(output=[Item(type="message", role="assistant", content=content)], output_text=content)


def calls(*pairs):
    return SimpleNamespace(
        output=[
            Item(type="function_call", call_id=f"c{i}", name=name, arguments=json.dumps(args))
            for i, (name, args) in enumerate(pairs, 1)
        ],
        output_text="",
    )


class FakeModel:
    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.responses = self

    async def create(self, **request):
        self.requests.append(json.loads(json.dumps(request)))
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def send_tool(sent):
    async def send(arguments):
        sent.append(arguments["to"])
        return f"Sent to {arguments['to']}."

    return Tool(
        "send_mail",
        "Send a message.",
        {"type": "object", "properties": {"to": {"type": "string"}}},
        send,
        outward=True,
        read_back=lambda a: f"Send the message to {a['to']}.",
    )


async def test_the_loop_holds_an_outward_call_instead_of_running_it():
    sent, held = [], []
    model = FakeModel(calls(("send_mail", {"to": "sam"})), text("Shall I? Say Abogi, approve."))
    items = []
    await run_turn(model, "m", "i", items, [send_tool(sent)], hold=lambda c, r: held.append((c.name, r)))
    assert sent == []
    assert held == [("send_mail", "Send the message to sam.")]
    output = next(i["output"] for i in items if i.get("type") == "function_call_output")
    assert "Send the message to sam." in output and "Abogi, approve" in output


async def test_without_a_hold_an_outward_call_is_refused():
    sent = []
    model = FakeModel(calls(("send_mail", {"to": "sam"})), text("I can't do that here."))
    items = []
    await run_turn(model, "m", "i", items, [send_tool(sent)])
    assert sent == []
    assert "cannot ask" in next(i["output"] for i in items if i.get("type") == "function_call_output")


async def test_only_one_step_is_held_per_turn():
    held = []
    model = FakeModel(calls(("send_mail", {"to": "a"}), ("send_mail", {"to": "b"})), text("One at a time."))
    items = []
    await run_turn(model, "m", "i", items, [send_tool([])], hold=lambda c, r: held.append(r))
    assert held == ["Send the message to a."]


# --- the hosted app runs the step on approval -------------------------------


def serve(model, tools):
    async def provider():
        return tools

    app = create_app(
        client_factory=lambda: model, model="m", tools_provider=provider, store=InMemoryResponseProvider(),
        skills_provider=lambda client, model: [], hosted_tools=[],
    )
    return TestClient(app)


def turn(client, words, previous=None):
    body = {"model": "x", "input": words}
    if previous:
        body["previous_response_id"] = previous
    return client.post("/responses", json=body).json()["id"]


def test_approval_on_the_next_turn_runs_the_held_step():
    sent = []
    model = FakeModel(
        calls(("send_mail", {"to": "sam"})),
        text("I'll send it to Sam. Say Abogi, approve."),
        text("Sent to Sam."),
    )
    with serve(model, [send_tool(sent)]) as client:
        first = turn(client, "Email Sam.")
        assert sent == []
        turn(client, "Abogi, approve.", first)
    assert sent == ["sam"]
    last_input = model.requests[-1]["input"]
    assert {"type": "function_call_output", "call_id": "c1", "output": "Sent to sam."} in last_input
    assert "approved that step" in model.requests[-1]["instructions"]


def test_any_other_turn_drops_the_held_step():
    sent = []
    model = FakeModel(calls(("send_mail", {"to": "sam"})), text("Say Abogi, approve."), text("Okay."))
    with serve(model, [send_tool(sent)]) as client:
        first = turn(client, "Email Sam.")
        second = turn(client, "Actually, what time is it?", first)
        turn(client, "Abogi, approve", second)
    assert sent == []
    assert "no step is waiting" in model.requests[-1]["instructions"]
    assert all(i.get("role") in ("user", "assistant") or "type" in i for i in model.requests[-1]["input"])


def test_a_dropped_step_is_named_so_the_model_can_hold_it_again():
    model = FakeModel(calls(("send_mail", {"to": "sam"})), text("Say Abogi, approve."), text("Say the phrase."))
    with serve(model, [send_tool([])]) as client:
        first = turn(client, "Email Sam.")
        turn(client, "yes", first)
    assert "Send the message to sam." in model.requests[-1]["instructions"]
    assert "dropped" in model.requests[-1]["instructions"]
