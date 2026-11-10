# Abogi

A voice-first personal chief of staff built on Microsoft Foundry. Abogi acts on its owner's behalf across mail, calendar, research and code, and speaks up when something needs attention.

**Status:** design agreed, nothing built yet.

## Shape

- **Core**: a hand-written agent loop in Python, deployed as a Foundry hosted agent, with voice, approvals, profile and history.
- **Skills**: personal ops, research to cited report, repo upkeep, issue to pull request.
- **Connectors**: MCP servers, each set up with your own credentials. Nothing personal lives in this repository.
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

## Licence

MIT
