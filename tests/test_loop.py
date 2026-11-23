"""The loop, driven by a scripted fake model so no network is needed."""

from types import SimpleNamespace

from abogi.core.loop import Tool, run_turn


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


class FakeClient:
    """Returns scripted responses in order; repeats the last one forever."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.responses = self

    async def create(self, **request):
        self.requests.append(request)
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


async def echo(arguments):
    return f"echo {arguments.get('word', '')}"


ECHO = Tool("echo", "Repeat a word.", {"type": "object", "properties": {}}, echo)


async def test_answer_without_tools_is_returned():
    client = FakeClient(text("Nothing on your calendar."))
    answer = await run_turn(client, "m", "be brief", [], [ECHO])
    assert answer == "Nothing on your calendar."
    assert len(client.requests) == 1


async def test_tool_result_is_fed_back_to_the_model():
    client = FakeClient(call("echo", '{"word": "hi"}'), text("It said hi."))
    items = []
    answer = await run_turn(client, "m", "be brief", items, [ECHO])
    assert answer == "It said hi."
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert outputs == [{"type": "function_call_output", "call_id": "c1", "output": "echo hi"}]


async def test_a_model_that_never_stops_calling_tools_is_stopped():
    client = FakeClient(call("echo"))  # asks for the same tool forever
    await run_turn(client, "m", "be brief", [], [ECHO], max_steps=4)
    assert len(client.requests) <= 5  # at most max_steps calls plus one wrap-up
    assert "tools" not in client.requests[-1]  # the last call had tools removed


def test_identical_repeated_calls_wrap_up_early():
    from abogi.core.loop import Action, LoopState, ToolCall, decide

    same = ToolCall("c1", "echo", "{}")
    assert decide(LoopState(2, 12, [same], [same])) is Action.RUN_TOOLS  # one retry allowed
    assert decide(LoopState(3, 12, [same], [same, same])) is Action.WRAP_UP
    changed = ToolCall("c2", "echo", "{\"word\": \"x\"}")
    assert decide(LoopState(3, 12, [changed], [same, same])) is Action.RUN_TOOLS


async def test_reasoning_effort_is_sent_only_when_set():
    client = FakeClient(text("ok"))
    await run_turn(client, "m", "be brief", [], [ECHO], reasoning_effort="low")
    assert client.requests[-1]["reasoning"] == {"effort": "low"}
    client = FakeClient(text("ok"))
    await run_turn(client, "m", "be brief", [], [ECHO])
    assert "reasoning" not in client.requests[-1]


class StreamingClient:
    """Streams scripted responses as Responses API events, like the real client."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.responses = self

    async def create(self, **request):
        self.requests.append(request)
        response, deltas = self.script.pop(0)

        async def events():
            for delta in deltas:
                yield SimpleNamespace(type="response.output_text.delta", delta=delta)
            yield SimpleNamespace(type="response.completed", response=response)

        return events()


async def test_stream_turn_yields_text_as_it_arrives():
    from abogi.core.loop import stream_turn

    client = StreamingClient((text("Two meetings today."), ["Two ", "meetings ", "today."]))
    chunks = [chunk async for chunk in stream_turn(client, "m", "be brief", [], [ECHO])]
    assert chunks == ["Two ", "meetings ", "today."]
    assert client.requests[0]["stream"] is True


async def test_stream_turn_runs_tools_and_separates_each_steps_words():
    from abogi.core.loop import stream_turn

    preamble = SimpleNamespace(
        output=[
            Item(type="message", role="assistant", content="Let me check."),
            Item(type="function_call", call_id="c1", name="echo", arguments="{\"word\": \"hi\"}"),
        ],
        output_text="Let me check.",
    )
    client = StreamingClient((preamble, ["Let me check."]), (text("It said hi."), ["It said hi."]))
    items = []
    chunks = [chunk async for chunk in stream_turn(client, "m", "be brief", items, [ECHO])]
    assert chunks == ["Let me check.", " ", "It said hi."]  # the space comes before the tool runs
    assert {"type": "function_call_output", "call_id": "c1", "output": "echo hi"} in items


async def test_a_failed_model_stream_raises():
    import pytest

    from abogi.core.loop import stream_turn

    class Failing(StreamingClient):
        async def create(self, **request):
            async def events():
                yield SimpleNamespace(type="response.failed", response=SimpleNamespace(error="quota"))

            return events()

    with pytest.raises(RuntimeError, match="quota"):
        [chunk async for chunk in stream_turn(Failing(), "m", "be brief", [], [])]


async def test_each_tool_run_is_a_span(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from abogi.core import loop

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(loop, "_tracer", provider.get_tracer("test"))  # a global provider is set once only

    async def broken(arguments):
        raise ValueError("no network")

    tools = [ECHO, Tool("broken", "Fails.", {"type": "object", "properties": {}}, broken)]
    client = FakeClient(
        SimpleNamespace(
            output=[
                Item(type="function_call", call_id="c1", name="echo", arguments="{}"),
                Item(type="function_call", call_id="c2", name="broken", arguments="{}"),
            ],
            output_text="",
        ),
        text("Done."),
    )
    await run_turn(client, "m", "be brief", [], tools)
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["execute_tool echo"].attributes["gen_ai.tool.call.id"] == "c1"
    assert spans["execute_tool broken"].status.status_code.name == "ERROR"
