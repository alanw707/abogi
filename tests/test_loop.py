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
