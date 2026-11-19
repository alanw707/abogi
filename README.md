# Abogi

A voice-first personal chief of staff built on Microsoft Foundry. Abogi acts on its owner's behalf across mail, calendar, research and code, and speaks up when something needs attention.

**Status:** step 1 works: a terminal question gets an answer from the owner's Google Calendar through the hosted agent, traced in Application Insights.

## Shape

- **Core**: a hand-written agent loop in Python, deployed as a Foundry hosted agent, with voice, approvals, profile and history.
- **Skills**: personal ops, research to cited report, repo upkeep, issue to pull request.
- **Connectors**: MCP servers where a usable one exists, each set up with your own credentials. Google Calendar uses its REST API for now ([ADR 0003](docs/adr/0003-google-calendar-api-before-mcp.md)). Nothing personal lives in this repository.
- **Channels**: a Windows tray voice client, and Discord when you are away.

Abogi works inside a sandbox and asks before any outward step, such as sending a message or opening a pull request.

The project vocabulary is in [GLOSSARY.md](GLOSSARY.md).

## Build order

1. Core loop over text with a calendar connector
2. Push-to-talk voice
3. Personal ops
4. Proactive behaviour and Discord
5. Research
6. Wake word
7. Repo upkeep
8. Issue to pull request

## Running it

One-time owner setup: `scripts/owner-setup.sh` walks through the Google OAuth client and Foundry tracing. Settings go in a git-ignored `.env`:

| Setting | Meaning |
|---|---|
| `ABOGI_PROJECT_ENDPOINT`, `FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model deployment, such as `gpt-5-mini` |
| `ABOGI_BACKEND` | `hosted` (Foundry model) or `local` (with `ABOGI_MODEL_BASE_URL`) |
| `ABOGI_REASONING_EFFORT` | `minimal`, `low` (default), `medium`, `high` or `default` |
| `ABOGI_TIMEZONE` | Owner's IANA time zone, such as `America/Los_Angeles` |
| `ABOGI_CALENDAR_API=google` | Read Google Calendar directly (needs a Google sign-in) |
| `ABOGI_GOOGLE_TOKEN_VAULT` | Key Vault URL holding the sign-in for the hosted agent |

Common commands (WSL, repo root):

```bash
uv run pytest -q                                                 # tests
uv run --env-file .env python -m abogi.connectors.google_auth    # Google sign-in; renew weekly while in testing mode
uv run --env-file .env python -m abogi.hosting.app               # agent server on :8088
uv run abogi "what's on my calendar tomorrow?"                   # ask the local server
uv run abogi --agent abogi "what's on my calendar tomorrow?"     # ask the hosted agent
uv run --env-file .env python scripts/deploy_agent.py <image>    # new hosted agent version
```

If Azure sign-in times out, an old `az` in `~/.local/bin` is shadowing `/usr/bin/az`; put `/usr/bin` first on `PATH`.

## Licence

MIT
