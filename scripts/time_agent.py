"""Time hosted agent turns, with the client's own sign-in kept out of the numbers.

Usage: uv run --env-file .env python scripts/time_agent.py [agent-name] [runs]
"""

from __future__ import annotations

import os
import sys
import time

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

QUESTIONS = [
    "What is on my calendar tomorrow?",
    "Who are you, in one sentence?",
    "What is on my calendar tomorrow?",
]


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "abogi"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else len(QUESTIONS)
    credential = DefaultAzureCredential()
    started = time.perf_counter()
    credential.get_token("https://ai.azure.com/.default")
    print(f"client sign-in: {time.perf_counter() - started:.1f}s (not counted below)")
    project = AIProjectClient(
        endpoint=os.environ["ABOGI_PROJECT_ENDPOINT"], credential=credential, allow_preview=True
    )
    client = project.get_openai_client(agent_name=name)
    for i in range(runs):
        question = QUESTIONS[i % len(QUESTIONS)]
        started = time.perf_counter()
        response = client.responses.create(input=question)
        took = time.perf_counter() - started
        print(f"{took:5.1f}s  {question}  ->  {len(response.output_text)} chars")


if __name__ == "__main__":
    main()
