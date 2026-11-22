"""The hosted agent: /responses in, run_turn, text answer out. No network."""

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from azure.ai.agentserver.responses import InMemoryResponseProvider

from abogi.core.loop import Tool
from abogi.hosting import backend
from abogi.hosting.app import build_input, create_app, default_tools_provider


class Item(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return dict(vars(self))


def text(content):
    return SimpleNamespace(
        output=[Item(type="message", role="assistant", content=content)],
        output_text=content,
    )


def call(name, arguments="{}", call_id="c1"):
    return SimpleNamespace(
        output=[Item(type="function_call", call_id=call_id, name=name, arguments=arguments)],
        output_text="",
    )


class FakeModel:
    """Scripted Responses API client; records every request it receives."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.responses = self

    async def create(self, **request):
        self.requests.append(json.loads(json.dumps(request)))  # snapshot: items mutate
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def serve(model, tools=None):
    async def provider():
        return list(tools or [])

    app = create_app(
        client_factory=lambda: model,
        model="test-model",
        tools_provider=provider,
        store=InMemoryResponseProvider(),
        skills_provider=lambda client, model: [],
        hosted_tools=[],
    )
    return TestClient(app)


def answer_text(body):
    return "".join(
        part["text"]
        for item in body["output"]
        if item["type"] == "message"
        for part in item["content"]
        if part["type"] == "output_text"
    )


# --- input mapping -------------------------------------------------------


def test_history_and_input_become_role_content_messages():
    history = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hi"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hello."}]},
    ]
    current = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Free at 3?"}]}]
    assert build_input(history, current) == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "Free at 3?"},
    ]


def test_items_without_text_are_dropped():
    current = [
        {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "x"}]},
        {"type": "function_call_output", "call_id": "c", "output": "x"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ok"}]},
    ]
    assert build_input([], current) == [{"role": "user", "content": "ok"}]


# --- request -> run_turn -> response --------------------------------------


def test_text_request_returns_the_loop_answer():
    model = FakeModel(text("Nothing on your calendar."))
    with serve(model) as client:
        reply = client.post("/responses", json={"model": "x", "input": "What's on today?"})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["status"] == "completed"
    assert answer_text(body) == "Nothing on your calendar."

    (request,) = model.requests
    assert request["model"] == "test-model"
    assert request["input"] == [{"role": "user", "content": "What's on today?"}]
    assert "approval" in request["instructions"].lower()


def test_tools_from_the_provider_are_offered_and_run():
    ran = []

    async def look(arguments):
        ran.append(arguments)
        return "Dentist at 3pm."

    tool = Tool("calendar_today", "Today's events.", {"type": "object", "properties": {}}, look)
    model = FakeModel(call("calendar_today", '{"day": "today"}'), text("Dentist at three."))
    with serve(model, [tool]) as client:
        reply = client.post("/responses", json={"model": "x", "input": "Anything today?"})

    assert answer_text(reply.json()) == "Dentist at three."
    assert ran == [{"day": "today"}]
    assert [t["name"] for t in model.requests[0]["tools"]] == ["calendar_today"]
    fed_back = model.requests[1]["input"]
    assert fed_back[-1] == {"type": "function_call_output", "call_id": "c1", "output": "Dentist at 3pm."}


def test_streaming_request_streams_the_answer():
    model = FakeModel(text("All clear."))
    with serve(model) as client:
        reply = client.post("/responses", json={"model": "x", "input": "Status?", "stream": True})
    assert reply.status_code == 200
    assert "response.output_text.delta" in reply.text
    assert "All clear." in reply.text
    assert "response.completed" in reply.text


def test_previous_response_is_sent_as_history():
    model = FakeModel(text("Noted."), text("You said lunch."))
    with serve(model) as client:
        first = client.post("/responses", json={"model": "x", "input": "Remember lunch."}).json()
        client.post(
            "/responses",
            json={"model": "x", "input": "What did I say?", "previous_response_id": first["id"]},
        )
    assert model.requests[1]["input"] == [
        {"role": "user", "content": "Remember lunch."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "What did I say?"},
    ]


# --- tools hook -----------------------------------------------------------


async def test_default_tools_provider_is_empty_without_connectors(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "abogi.connectors", None)  # import fails
    assert await default_tools_provider() == []


async def test_default_tools_provider_uses_connectors_load_tools(monkeypatch):
    import sys
    import types

    tool = Tool("t", "d", {"type": "object"}, None)
    fake = types.ModuleType("abogi.connectors")

    async def load_tools():
        return [tool]

    fake.load_tools = load_tools
    monkeypatch.setitem(sys.modules, "abogi.connectors", fake)
    assert await default_tools_provider() == [tool]


async def test_default_tools_provider_ignores_connectors_without_load_tools(monkeypatch):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "abogi.connectors", types.ModuleType("abogi.connectors"))
    assert await default_tools_provider() == []


async def test_tools_are_reused_once_loaded():
    from abogi.hosting.app import reuse_once_loaded

    tool = Tool("t", "d", {"type": "object"}, None)
    results = [[], [tool], [Tool("other", "d", {}, None)]]
    calls = []

    async def provider():
        calls.append(1)
        return results[len(calls) - 1]

    tools = reuse_once_loaded(provider)
    assert await tools() == []  # nothing yet: try again next turn
    assert await tools() == [tool]
    assert await tools() == [tool]  # reused, provider not called again
    assert len(calls) == 2


# --- backend selection ----------------------------------------------------


def test_local_backend_uses_base_url():
    client = backend.make_client({"ABOGI_BACKEND": "local", "ABOGI_MODEL_BASE_URL": "http://localhost:5273/v1"})
    assert str(client.base_url).rstrip("/") == "http://localhost:5273/v1"


def test_local_backend_requires_base_url():
    with pytest.raises(RuntimeError, match="ABOGI_MODEL_BASE_URL"):
        backend.make_client({"ABOGI_BACKEND": "local"})


def test_hosted_backend_uses_project_endpoint():
    endpoint = "https://example.services.ai.azure.com/api/projects/p"
    client = backend.make_client({"ABOGI_BACKEND": "hosted", "FOUNDRY_PROJECT_ENDPOINT": endpoint})
    assert str(client.base_url).rstrip("/") == endpoint + "/openai/v1"


def test_hosted_backend_requires_endpoint():
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        backend.make_client({"ABOGI_BACKEND": "hosted"})


def test_backend_defaults_to_hosted_when_endpoint_present():
    assert backend.backend_name({"FOUNDRY_PROJECT_ENDPOINT": "https://x"}) == "hosted"
    assert backend.backend_name({}) == "local"


def test_unknown_backend_is_rejected():
    with pytest.raises(RuntimeError, match="ABOGI_BACKEND"):
        backend.make_client({"ABOGI_BACKEND": "cloud"})


def test_model_name_comes_from_deployment_env():
    assert backend.model_name({"AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-x"}) == "gpt-x"
    with pytest.raises(RuntimeError, match="AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        backend.model_name({})


def test_instructions_name_only_the_connected_tools():
    from abogi.hosting.instructions import INSTRUCTIONS, with_capabilities

    none = with_capabilities(INSTRUCTIONS, [])
    assert "no tools connected" in none
    some = with_capabilities(INSTRUCTIONS, ["list_events", "get_event"])
    assert "list_events, get_event" in some
    assert "never say these names aloud" in some
    assert "no tools connected" not in some
    assert "Abogi, approve" in INSTRUCTIONS
    assert "AM or PM" in INSTRUCTIONS


def test_instructions_state_the_model_so_it_never_guesses():
    from abogi.hosting.instructions import with_model

    text = with_model("x", "gpt-5-mini")
    assert "gpt-5-mini" in text
    assert "do not guess" in text


def test_reasoning_effort_setting():
    import pytest

    from abogi.hosting.backend import reasoning_effort

    assert reasoning_effort({}) == "low"
    assert reasoning_effort({"ABOGI_REASONING_EFFORT": "Minimal"}) == "minimal"
    assert reasoning_effort({"ABOGI_REASONING_EFFORT": "default"}) is None
    with pytest.raises(RuntimeError):
        reasoning_effort({"ABOGI_REASONING_EFFORT": "fast"})


def test_turn_instructions_carry_the_owners_local_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from abogi.hosting.instructions import with_now

    now = datetime(2026, 11, 18, 14, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert "Wednesday 18 November 2026, 2:05 PM (America/Los_Angeles)" in with_now("x", now)


def test_owner_time_zone_setting():
    import pytest

    from abogi.hosting.backend import owner_time_zone

    assert owner_time_zone({}) == "UTC"
    assert owner_time_zone({"ABOGI_TIMEZONE": "Asia/Taipei"}) == "Asia/Taipei"
    with pytest.raises(RuntimeError):
        owner_time_zone({"ABOGI_TIMEZONE": "Mars/Base"})


def test_a_lead_in_is_its_own_message_before_the_tools_run():
    preamble = SimpleNamespace(
        output=[
            Item(type="message", role="assistant", content="Let me check."),
            Item(type="function_call", call_id="c1", name="look", arguments="{}"),
        ],
        output_text="Let me check.",
    )

    async def look(arguments):
        return "Dentist at 3pm."

    tool = Tool("look", "Look.", {"type": "object", "properties": {}}, look)
    model = FakeModel(preamble, text("Dentist at three."))
    with serve(model, [tool]) as client:
        reply = client.post("/responses", json={"model": "x", "input": "Anything?"}).json()
    texts = [part["text"] for item in reply["output"] for part in item["content"]]
    assert texts == ["Let me check.", "Dentist at three."]
