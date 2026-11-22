# Abogi

A voice-first personal chief of staff built on Microsoft Foundry. Abogi acts on its owner's behalf across mail, calendar, research and code, and speaks up when something needs attention.

**Status:** step 2 works: hold F9 on Windows (or turn on hands-free) and ask about your calendar; the hosted agent answers aloud through Voice Live, streaming its answer, and each turn, with its tool and Google calls, is traced in Application Insights.

## Shape

- **Core**: a hand-written agent loop in Python, deployed as a Foundry hosted agent, with voice, approvals, profile and history.
- **Skills**: personal ops, research to cited report, repo upkeep, issue to pull request.
- **Connectors**: MCP servers where a usable one exists, each set up with your own credentials. Google Calendar uses its REST API for now ([ADR 0003](docs/adr/0003-google-calendar-api-before-mcp.md)). Nothing personal lives in this repository.
- **Channels**: a Windows tray voice client, and Discord when you are away.

Abogi works inside a sandbox. Any outward step, such as sending an email or moving a meeting, is held by the core and read back; it runs only when the owner says "Abogi, approve" ([ADR 0004](docs/adr/0004-approvals-enforced-in-code.md)).

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
| `ABOGI_MAIL_API=google` | Read Gmail, write drafts, and send them after approval (same Google sign-in) |
| `ABOGI_ALERT_MINUTES` | Alert when a meeting starts within this many minutes (default 10) |
| `ABOGI_ALERT_FROM` | Comma-separated senders whose unread mail is an alert |

Common commands (WSL, repo root):

```bash
uv run pytest -q                                                 # tests
uv run --env-file .env python -m abogi.connectors.google_auth    # Google sign-in (calendar + Gmail); renew weekly in testing mode
uv run --env-file .env python -m abogi.hosting.app               # agent server on :8088
uv run abogi "what's on my calendar tomorrow?"                   # ask the local server
uv run abogi --agent abogi "what's on my calendar tomorrow?"     # ask the hosted agent
uv run --env-file .env python scripts/deploy_agent.py <image>    # new hosted agent version
```

### Voice (Windows tray, push-to-talk)

Needs `uv` on Windows (`winget install astral-sh.uv`), `ABOGI_TENANT_ID` in `.env`, and `Cognitive Services User` plus `Foundry User` on the Foundry resource. From Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File <repo>\scripts\abogi-tray.ps1   # e.g. \\wsl.localhost\<distro>\home\<you>\projects\abogi
```

The first run prints a code to enter at microsoft.com/devicelogin to sign in to Azure; later runs are silent. Hold F9 (`ABOGI_VOICE_KEY`) and speak, release to send, press again to interrupt. The tray dot is grey idle, blue warming, red listening, amber thinking, green speaking, black offline. For hands-free, right-click the tray dot and tick "Hands-free listening" (or start with `ABOGI_VOICE_MODE=hands-free`): just talk, and F9 only stops Abogi mid-answer. While it is on, everything the microphone hears is streamed to Azure. For a wake word, tick "Wake word ("Hey Jarvis")" (or start with `ABOGI_VOICE_MODE=wake-word`): openWakeWord listens on this computer and nothing is streamed until it hears "Hey Jarvis"; then one question streams, and saying it again interrupts an answer. The tray runs on Python 3.14 for this (onnxruntime has no 3.15 wheels yet); the launcher handles it. Abogi also speaks first: a briefing at each time in `ABOGI_BRIEFING_TIMES` (such as `07:30,17:45`), and alerts for meetings about to start or mail from `ABOGI_ALERT_FROM`, checked every `ABOGI_ALERT_EVERY` seconds (default 300, `off` to stop; the check runs in code, not the model). It waits until it is idle, and you can interrupt it like any answer. Optional: `ABOGI_VOICE` (an Azure voice name) and `ABOGI_AGENT_NAME`.

If Azure sign-in times out, an old `az` in `~/.local/bin` is shadowing `/usr/bin/az`; put `/usr/bin` first on `PATH`.

## Licence

MIT
