from types import SimpleNamespace

import pytest

from abogi import cli


class FakeResponses:
    def __init__(self, replies, ids=True):
        self.replies = list(replies)
        self.ids = ids
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        return SimpleNamespace(
            id=f"resp_{n}" if self.ids else None, output_text=self.replies[n - 1]
        )


def fake_client(replies, ids=True):
    return SimpleNamespace(responses=FakeResponses(replies, ids))


def run(argv, client, lines=()):
    out = []
    feed = iter(lines)

    def read(_prompt):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    code = cli.main(argv, client_factory=lambda args: client, read=read, write=out.append)
    return code, out


def test_one_shot_prints_the_answer():
    client = fake_client(["You have standup at 9."])
    code, out = run(["what's on my calendar tomorrow"], client)
    assert code == 0
    assert out == ["You have standup at 9."]
    (call,) = client.responses.calls
    assert call["input"] == [{"role": "user", "content": "what's on my calendar tomorrow"}]
    assert "previous_response_id" not in call
    assert "model" not in call


def test_repl_chains_previous_response_id():
    client = fake_client(["first", "second"])
    code, out = run([], client, ["hello", "", "and then?", "exit"])
    assert code == 0
    assert out == ["first", "second"]
    first, second = client.responses.calls
    assert "previous_response_id" not in first
    assert second["previous_response_id"] == "resp_1"
    assert second["input"] == [{"role": "user", "content": "and then?"}]


def test_repl_resends_history_when_server_gives_no_ids():
    client = fake_client(["first", "second"], ids=False)
    run([], client, ["hello", "and then?"])
    second = client.responses.calls[1]
    assert "previous_response_id" not in second
    assert second["input"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "and then?"},
    ]


def test_model_is_passed_when_given():
    client = fake_client(["ok"])
    run(["--model", "gpt-x", "hi"], client)
    assert client.responses.calls[0]["model"] == "gpt-x"


def test_target_flags():
    local = cli.parse_args(["hi"])
    assert local.agent is None and local.url == "http://localhost:8088"
    hosted = cli.parse_args(["--agent", "abogi", "hi"])
    assert hosted.agent == "abogi"
    with pytest.raises(SystemExit):
        cli.parse_args(["--local", "--agent", "abogi"])


def test_hosted_needs_endpoint(monkeypatch):
    monkeypatch.delenv(cli.ENDPOINT_ENV, raising=False)
    with pytest.raises(SystemExit, match=cli.ENDPOINT_ENV):
        cli.make_client(cli.parse_args(["--agent", "abogi", "hi"]))


def test_local_client_points_at_local_server():
    client = cli.make_client(cli.parse_args(["hi"]))
    assert str(client.base_url).rstrip("/") == "http://localhost:8088"


def test_hosted_client_uses_agent_endpoint(monkeypatch):
    monkeypatch.setenv(cli.ENDPOINT_ENV, "https://example.services.ai.azure.com/api/projects/p")
    client = cli.make_client(cli.parse_args(["--agent", "abogi", "hi"]))
    assert str(client.base_url).rstrip("/") == (
        "https://example.services.ai.azure.com/api/projects/p/agents/abogi/endpoint/protocols/openai"
    )
