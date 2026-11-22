"""The Discord relay: owner-only DMs, one conversation per channel, alerts once."""

import json
from types import SimpleNamespace

from abogi.channels.discord import Relay, split_message
from abogi.channels.proactive import AlertQueue
from abogi.skills.alerts import ALERT_CHECK

OWNER = 42


class Agent:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []
        self.responses = self

    def create(self, **request):
        self.requests.append(request)
        n = len(self.requests)
        return SimpleNamespace(id=f"resp_{n}", output_text=self.answers.pop(0))


def test_only_the_owners_direct_messages_are_answered():
    relay = Relay(Agent([]), OWNER)
    assert relay.accepts(OWNER, is_dm=True, is_bot=False)
    assert not relay.accepts(7, is_dm=True, is_bot=False)  # someone else
    assert not relay.accepts(OWNER, is_dm=False, is_bot=False)  # in a server channel
    assert not relay.accepts(OWNER, is_dm=True, is_bot=True)


async def test_each_channel_is_one_conversation_so_approvals_follow_the_read_back():
    agent = Agent(["Send it to Sam? Say Abogi, approve.", "Sent."])
    relay = Relay(agent, OWNER)
    await relay.answer(1, "Email Sam that I am late.")
    assert await relay.answer(1, "Abogi, approve") == "Sent."
    assert "previous_response_id" not in agent.requests[0]
    assert agent.requests[1]["previous_response_id"] == "resp_1"


async def test_alerts_are_sent_once():
    alert = {"alerts": [{"id": "event:e1", "text": "Dentist starts at 2:00 PM, in 8 minutes."}]}
    agent = Agent([json.dumps(alert), json.dumps(alert)])
    relay, queue = Relay(agent, OWNER), AlertQueue()
    assert await relay.check_alerts(queue) == ["Dentist starts at 2:00 PM, in 8 minutes."]
    assert await relay.check_alerts(queue) == []
    assert agent.requests[0] == {"input": ALERT_CHECK}


def test_long_replies_are_split_at_line_breaks_under_the_limit():
    text = ("line " * 30 + "\n") * 30
    pieces = split_message(text, limit=500)
    assert all(len(p) <= 500 for p in pieces)
    assert "".join(p.replace("\n", "") for p in pieces).replace(" ", "") == text.replace("\n", "").replace(" ", "")
    assert split_message("") == ["(no answer)"]
