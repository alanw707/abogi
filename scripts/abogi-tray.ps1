# Start the Abogi push-to-talk tray client on Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\abogi-tray.ps1
# Needs uv on Windows (winget install astral-sh.uv). The repo may live in WSL;
# Windows gets its own virtual environment so the WSL .venv is left alone.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'abogi\venv'
$env:UV_LINK_MODE = 'copy'  # hard links do not work across the WSL share
Set-Location $repo
# Python 3.14: the wake word's onnxruntime has no 3.15 wheels yet. openwakeword is
# added here, not in pyproject: its Linux-only tflite-runtime breaks the
# cross-platform lock, and only this Windows client needs it.
uv run --python 3.14 --extra tray --with "openwakeword>=0.6" --env-file .env python -m abogi.channels.tray
