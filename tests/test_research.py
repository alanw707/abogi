"""Research: a cited report from web search, summary aloud, full report in drafts."""

import json
from types import SimpleNamespace

import anyio

from abogi.core.loop import run_turn
from abogi.skills.research import ResearchSkill, hosted_tools, summary_of

REPORT = """Powerwall 3 stores more per unit; the IQ 5P is modular and smaller.
Both qualify for the federal credit.

## Findings
- Powerwall 3 is 13.5 kWh ([tesla.com](https://tesla.com))

## Sources
- https://tesla.com
"""


class Model:
    def __init__(self, text):
        self.text = text
        self.requests = []
        self.responses = self

    async def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(output_text=self.text, output=[])


class Mail:
    def __init__(self, fail=False):
        self.saved = []
        self.fail = fail

    async def save_report(self, subject, markdown):
        if self.fail:
            raise RuntimeError("403 insufficient scopes")
        self.saved.append((subject, markdown))
        return "Draft saved."


def test_the_summary_is_the_opening_lines_without_links():
    assert summary_of(REPORT) == (
        "Powerwall 3 stores more per unit; the IQ 5P is modular and smaller. Both qualify for the federal credit."
    )


def test_research_searches_saves_the_report_and_returns_the_summary():
    model, mail = Model(REPORT), Mail()
    tool = ResearchSkill(model, "m", mail).tool()
    result = anyio.run(tool.run, {"question": "Powerwall 3 or IQ 5P?"})
    assert model.requests[0]["tools"] == [{"type": "web_search"}]
    assert model.requests[0]["reasoning"] == {"effort": "low"}
    assert result.startswith("Summary: Powerwall 3 stores more")
    assert "email drafts" in result
    subject, body = mail.saved[0]
    assert subject == "Research: Powerwall 3 or IQ 5P?" and "## Sources" in body
    assert not tool.outward  # a draft to oneself stays in the sandbox


def test_a_failed_save_still_gives_the_summary():
    result = anyio.run(ResearchSkill(Model(REPORT), "m", Mail(fail=True)).tool().run, {"question": "q"})
    assert result.startswith("Summary:") and "could not be saved" in result


def test_web_search_is_on_unless_turned_off():
    assert hosted_tools({}) == [{"type": "web_search"}]
    assert hosted_tools({"ABOGI_WEB_SEARCH": "off"}) == []


async def test_platform_tools_are_offered_next_to_function_tools():
    class Fake:
        def __init__(self):
            self.requests = []
            self.responses = self

        async def create(self, **request):
            self.requests.append(json.loads(json.dumps(request)))
            return SimpleNamespace(output=[], output_text="Python 3.15.2, per python.org.")

    model = Fake()
    await run_turn(model, "m", "i", [], [], hosted_tools=[{"type": "web_search"}])
    assert model.requests[0]["tools"] == [{"type": "web_search"}]
