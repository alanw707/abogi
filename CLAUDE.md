# Abogi

Voice-first chief of staff on Microsoft Foundry. Start with [README.md](README.md); terms are in [GLOSSARY.md](GLOSSARY.md), decisions in [docs/adr/](docs/adr/).

## Working here

- The repo lives in WSL. From Windows, run commands as `wsl -e bash -lc '...'`, starting with `export PATH=/usr/bin:$HOME/.local/bin:$PATH`. For anything with quotes or several lines, write a script file and run that.
- Check before committing with `uv run ruff check . && uv run pytest -q`. CI runs the same.
- Owner data and secrets stay out of the repo: `.env`, `profile/` and `history/` are git-ignored. A token goes from the clipboard to its store with `scripts/save-secret.sh`, never through chat.
- `.env` is read by `uv run --env-file .env`. Keep it LF-only, and leave it out of `source`: a `;` in a value breaks the shell.

## Where things are

- Deploy the hosted agent: `scripts/deploy.sh` (in WSL).
- Voice tray (Windows): `scripts/abogi-tray.ps1`. Its log is `%LOCALAPPDATA%\abogi\tray.log`: search that file for what the tray did.
- Discord bot: `scripts/abogi-discord.sh` (in WSL).
- Custom wake word: [docs/wake-word.md](docs/wake-word.md).
- What Abogi may say about itself: `src/abogi/hosting/instructions.py`. A tool's description is how Abogi learns it has that ability.
