# Abogi

A voice-first personal chief of staff built on Microsoft Foundry. Abogi acts on its owner's behalf across mail, calendar, research and code, and speaks up when something needs attention.

**Status:** all eight steps are built. By voice (push-to-talk, hands-free or "Hey Jarvis") or Discord, Abogi reads and changes your calendar, triages and drafts mail, researches with cited reports, briefs you on a schedule, alerts you when something needs you, and looks after your GitHub repositories, handing issues to Copilot's coding agent for pull requests. Every outward step waits for "Abogi, approve".

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
| `ABOGI_GITHUB_REPOS` | Repositories Abogi looks after (`owner/name, ...`); token in `ABOGI_GITHUB_TOKEN` or Key Vault secret `github-token` |
| `ABOGI_DISCORD_SERVERS` | Discord servers Abogi may read and manage (numeric ids, comma-separated); bot token in `ABOGI_DISCORD_TOKEN` or Key Vault secret `discord-token` |
| `ABOGI_WEB_SEARCH=off` | Turn off web search and research |
| `ABOGI_ALERT_MINUTES` | Alert when a meeting starts within this many minutes (default 10) |
| `ABOGI_ALERT_FROM` | Comma-separated senders whose unread mail is an alert |

Common commands (WSL, repo root):

```bash
uv run pytest -q                                                 # tests
uv run --env-file .env python -m abogi.connectors.google_auth    # Google sign-in (calendar + Gmail); renew weekly in testing mode
uv run --env-file .env python -m abogi.hosting.app               # agent server on :8088
uv run abogi "what's on my calendar tomorrow?"                   # ask the local server
uv run abogi --agent abogi "what's on my calendar tomorrow?"     # ask the hosted agent
scripts/deploy.sh                                                # build, push and deploy a new hosted agent version
```

### Voice (Windows tray, push-to-talk)

Needs `uv` on Windows (`winget install astral-sh.uv`), `ABOGI_TENANT_ID` in `.env`, and `Cognitive Services User` plus `Foundry User` on the Foundry resource. From Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File <repo>\scripts\abogi-tray.ps1   # e.g. \\wsl.localhost\<distro>\home\<you>\projects\abogi
```

The first run prints a code to enter at microsoft.com/devicelogin to sign in to Azure; later runs are silent. Hold F9 (`ABOGI_VOICE_KEY`) and speak, release to send, press again to interrupt. The tray dot is grey idle, blue warming, red listening, amber thinking, green speaking, black offline. For hands-free, right-click the tray dot and tick "Hands-free listening" (or start with `ABOGI_VOICE_MODE=hands-free`): just talk, and F9 only stops Abogi mid-answer. While it is on, everything the microphone hears is streamed to Azure. For a wake word, tick "Wake word ("Hey Jarvis")" (or start with `ABOGI_VOICE_MODE=wake-word`): openWakeWord listens on this computer and nothing is streamed until it hears "Hey Jarvis"; then one question streams, and saying it again interrupts an answer. For your own wake word, train a model with [openWakeWord's Colab notebook](https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing) and set `ABOGI_WAKE_WORD` to the downloaded `.onnx` file (the file name, such as `hey_abboggy.onnx`, becomes the phrase shown in the tray); see [docs/wake-word.md](docs/wake-word.md). The tray runs on Python 3.14 for this (onnxruntime has no 3.15 wheels yet); the launcher handles it. Abogi also speaks first: a briefing at each time in `ABOGI_BRIEFING_TIMES` (such as `07:30,17:45`), and alerts for meetings about to start or mail from `ABOGI_ALERT_FROM`, checked every `ABOGI_ALERT_EVERY` seconds (default 300, `off` to stop; the check runs in code, not the model). It waits until it is idle, and you can interrupt it like any answer. Optional: `ABOGI_VOICE` (an Azure voice name) and `ABOGI_AGENT_NAME`.

### Profile and history

Abogi keeps a short profile of facts and preferences (added to every turn) and a history of the steps it ran after approval, as Key Vault secrets `abogi-profile` and `abogi-history` (or under `~/.config/abogi` without a vault), never in this repository. Edit the profile with `uv run --env-file .env python -m abogi.profile show|add|edit|history`. For Abogi to save "remember that..." and its history itself, the hosted agent's identity needs `Key Vault Secrets Officer` on that vault.

### Coding sessions (read-only)

Abogi can tell you what your Claude Code and Codex sessions on your computer are working on. The hosted agent cannot reach your computer, so your computer reports to it: with `ABOGI_SESSIONS=on` in `.env`, the tray saves a short digest every five minutes as the Key Vault secret `abogi-sessions`, and the agent reads it. The digest holds the 12 most recent sessions from the last two weeks: the tool, the folder name, the title, when it was last active, your last request and the last reply, each cut short, with anything shaped like a key or token removed. Abogi cannot start, steer or change a session.

- See what would be shared, without sharing it: `uv run --env-file .env python -m abogi.sessions preview`
- Share once by hand, see what Abogi can read, or remove it: `... python -m abogi.sessions share|show|clear`
- Sessions kept elsewhere (such as the WSL home, from Windows): list the folders in `ABOGI_SESSION_DIRS`, separated by `;`.

### Discord (when away)

`scripts/abogi-discord.sh` (`uv run --extra discord --env-file .env python -m abogi.channels.discord`) answers direct messages from `ABOGI_DISCORD_OWNER_ID` only, using a bot token in `ABOGI_DISCORD_TOKEN` (enable the Message Content intent). Approvals work the same way: type "Abogi, approve". `ABOGI_DISCORD_ALERTS=on` also sends alerts; use it where the tray is not running.

### Discord servers (read and manage)

Separate from the direct-message channel above: with `ABOGI_DISCORD_SERVERS` set, Abogi can list a server's channels and read their latest messages by voice or DM. After "Abogi, approve" it can post, create, rename or delete a channel, and delete a message.

- It acts as the bot, so each server decides what is allowed. Reading needs View Channels and Read Message History; posting needs Send Messages; channel changes need Manage Channels; deleting others' messages needs Manage Messages.
- The hosted agent reads the bot token from Key Vault: `scripts/save-secret.sh discord` saves a new token to both places, and `scripts/save-secret.sh discord-vault` copies the one already in `.env`.
- Posts never ping `@everyone` or a role.

If Azure sign-in times out, an old `az` in `~/.local/bin` is shadowing `/usr/bin/az`; put `/usr/bin` first on `PATH`.

## Licence

MIT
