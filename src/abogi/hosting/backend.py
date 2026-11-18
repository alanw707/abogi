"""Where the model lives: a Foundry project deployment, or a local endpoint.

`make_client()` returns an async client that speaks the Responses API, which
is all the core loop needs. The choice is made by environment variables so
the same image runs hosted and on a laptop.

- ``ABOGI_BACKEND``: ``hosted`` or ``local``. Defaults to ``hosted`` when
  ``FOUNDRY_PROJECT_ENDPOINT`` is set (the platform injects it), else ``local``.
- hosted: ``FOUNDRY_PROJECT_ENDPOINT``, authenticated with
  ``DefaultAzureCredential`` (managed identity in the container, ``az login``
  on a laptop).
- local: ``ABOGI_MODEL_BASE_URL`` (Foundry Local or any OpenAI-compatible
  server, e.g. ``http://localhost:5273/v1``) and optional ``ABOGI_MODEL_API_KEY``.
- both: ``AZURE_AI_MODEL_DEPLOYMENT_NAME`` names the model. The ``FOUNDRY_``
  prefix is reserved for the platform, so it is not used here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

BACKENDS = ("hosted", "local")


def backend_name(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    name = env.get("ABOGI_BACKEND", "").strip().lower()
    if not name:
        return "hosted" if env.get("FOUNDRY_PROJECT_ENDPOINT") else "local"
    if name not in BACKENDS:
        raise RuntimeError(f"ABOGI_BACKEND must be one of {', '.join(BACKENDS)}, not {name!r}.")
    return name


def owner_time_zone(env: Mapping[str, str] | None = None) -> str:
    """ABOGI_TIMEZONE: the owner's IANA time zone, such as America/Los_Angeles. Default UTC."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    env = os.environ if env is None else env
    zone = env.get("ABOGI_TIMEZONE", "").strip() or "UTC"
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise RuntimeError(f"ABOGI_TIMEZONE is not a known time zone: {zone!r}.") from error
    return zone


def model_name(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    model = env.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
    if not model:
        raise RuntimeError("Set AZURE_AI_MODEL_DEPLOYMENT_NAME to the model deployment to use.")
    return model


REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


def reasoning_effort(env: Mapping[str, str] | None = None) -> str | None:
    """ABOGI_REASONING_EFFORT: one of REASONING_EFFORTS, or "default" to send none.

    Defaults to "low": measured on gpt-5-mini it is as fast as the model default
    while capping thinking on harder turns. "default" is for models that reject
    the parameter (some local models).
    """
    env = os.environ if env is None else env
    effort = env.get("ABOGI_REASONING_EFFORT", "low").strip().lower() or "low"
    if effort == "default":
        return None
    if effort not in REASONING_EFFORTS:
        allowed = ", ".join(sorted(REASONING_EFFORTS))
        raise RuntimeError(f"ABOGI_REASONING_EFFORT must be one of {allowed} or default, not {effort!r}.")
    return effort


def make_client(env: Mapping[str, str] | None = None) -> Any:
    """Return an async OpenAI-compatible client for the selected backend."""
    env = os.environ if env is None else env
    if backend_name(env) == "hosted":
        return _hosted_client(env)
    return _local_client(env)


def _hosted_client(env: Mapping[str, str]) -> Any:
    endpoint = env.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("The hosted backend needs FOUNDRY_PROJECT_ENDPOINT.")
    from azure.ai.projects.aio import AIProjectClient
    from azure.identity.aio import DefaultAzureCredential

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    # The returned AsyncOpenAI client carries a bearer-token provider bound to
    # the credential, so it keeps working for the life of the process.
    return project.get_openai_client()


def _local_client(env: Mapping[str, str]) -> Any:
    base_url = env.get("ABOGI_MODEL_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError("The local backend needs ABOGI_MODEL_BASE_URL, e.g. http://localhost:5273/v1.")
    from openai import AsyncOpenAI

    # Local servers usually ignore the key, but the client insists on one.
    return AsyncOpenAI(base_url=base_url, api_key=env.get("ABOGI_MODEL_API_KEY") or "not-needed")
