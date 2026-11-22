"""Research to a cited report (step 5).

`research` is a tool the main loop can call. It runs its own model call with
the platform's web search, at low reasoning effort (about 12 s with one or two
searches; higher effort hit the deployment's token rate limit), and asks for
a Markdown report with sources. The owner hears a short summary; the full
report is saved as a Gmail draft addressed to the owner, which stays inside
the sandbox (a draft to oneself is not an outward step).

Quick factual questions do not need this: the main loop also has web search
directly (see `hosted_tools()`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from abogi.core.loop import Tool

logger = logging.getLogger(__name__)

WEB_SEARCH = {"type": "web_search"}

REPORT_INSTRUCTIONS = """\
Research the owner's question with web search. Write a report in Markdown:
- First line: a two-sentence summary that answers the question, with no links \
(it is read aloud).
- Then "## Findings": the key points, each with its source as a Markdown link.
- Then "## Sources": every page you used.
Prefer primary and recent sources; say when sources disagree or are old.
"""


def hosted_tools(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Platform tools for the main loop: web search, unless ABOGI_WEB_SEARCH=off."""
    env = os.environ if env is None else env
    return [] if (env.get("ABOGI_WEB_SEARCH") or "").lower() == "off" else [WEB_SEARCH]


def summary_of(report: str) -> str:
    """The report's opening lines, up to the first heading: what is read aloud."""
    lines = []
    for line in report.strip().splitlines():
        if line.startswith("#"):
            if lines:
                break
            continue
        if line.strip():
            lines.append(line.strip())
    return " ".join(lines)


class ResearchSkill:
    def __init__(self, client: Any, model: str, mail: Any = None) -> None:
        self._client = client
        self._model = model
        self._mail = mail  # a connector with save_report(subject, markdown), or None

    def tool(self) -> Tool:
        return Tool(
            "research",
            "Research a question on the web and write a cited report. Use for questions "
            "that need several sources or a comparison; quick facts do not need it. "
            "Takes about 15 seconds. The full report is saved to the owner's email drafts.",
            {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "The question, in full."}},
                "required": ["question"],
            },
            self._run,
        )

    async def _run(self, arguments: dict[str, Any]) -> str:
        question = str(arguments["question"])
        response = await self._client.responses.create(
            model=self._model,
            instructions=REPORT_INSTRUCTIONS,
            input=question,
            tools=[WEB_SEARCH],
            reasoning={"effort": "low"},
        )
        report = response.output_text or ""
        if not report:
            return "The research found nothing usable."
        summary = summary_of(report)
        where = await self._save(question, report)
        return f"Summary: {summary}\n{where}"

    async def _save(self, question: str, report: str) -> str:
        if self._mail is None or not hasattr(self._mail, "save_report"):
            return "The full report could not be saved: mail is not connected."
        try:
            await self._mail.save_report(f"Research: {question[:80]}", f"# {question}\n\n{report}")
        except Exception as error:  # noqa: BLE001 - the summary is still worth saying
            logger.warning("Saving the research report failed: %s: %s", type(error).__name__, error)
            return f"The full report could not be saved ({type(error).__name__}: {error})."
        return "The full report, with sources, is in the owner's email drafts."
