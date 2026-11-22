"""Create a new hosted agent version from an image already pushed to a registry.

Usage: uv run python scripts/deploy_agent.py <image> [agent-name]
Needs ABOGI_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME, and an Azure
sign-in with the Foundry User role on the project.
"""

from __future__ import annotations

import os
import sys
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import DefaultAzureCredential


PASSED_THROUGH = (
    "ABOGI_CALENDAR_API",
    "ABOGI_MAIL_API",
    "ABOGI_ALERT_MINUTES",
    "ABOGI_ALERT_FROM",
    "ABOGI_WEB_SEARCH",
    "ABOGI_GITHUB_REPOS",
    "ABOGI_PROFILE_VAULT",
    "ABOGI_GITHUB_TOKEN_VAULT",
    "ABOGI_GITHUB_TOKEN_SECRET",
    "ABOGI_TIMEZONE",
    "ABOGI_REASONING_EFFORT",
    "ABOGI_GOOGLE_TOKEN_VAULT",
    "ABOGI_GOOGLE_TOKEN_SECRET",
)


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    image = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "abogi"
    project = AIProjectClient(
        endpoint=os.environ["ABOGI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    definition = HostedAgentDefinition(
        cpu="0.5",
        memory="1Gi",
        container_configuration=ContainerConfiguration(image=image),
        protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
        environment_variables={
            "PYTHONNOUSERSITE": "1",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
            "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING": "true",
            # Settings only, never secrets: the Google sign-in lives in Key Vault.
            **{name: os.environ[name] for name in PASSED_THROUGH if os.environ.get(name)},
        },
    )
    version = project.agents.create_version(agent_name=name, definition=definition)
    print(f"created {name} version {version.version}")

    for _ in range(60):  # up to ten minutes
        version = project.agents.get_version(agent_name=name, agent_version=version.version)
        status = str(getattr(version.status, "value", version.status)).lower()
        print(f"status: {status}")
        if status in {"active", "failed"}:
            return 0 if status == "active" else 1
        time.sleep(10)
    print("still not active after ten minutes")
    return 1


if __name__ == "__main__":
    sys.exit(main())
