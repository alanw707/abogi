#!/usr/bin/env bash
# Run Abogi on Discord (answers the owner's direct messages only). Run in WSL.
export PATH="/usr/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.." && exec uv run --extra discord --env-file .env python -m abogi.channels.discord
