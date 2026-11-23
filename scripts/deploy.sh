#!/usr/bin/env bash
# Build the agent image, push it, and make it the new hosted agent version.
#   scripts/deploy.sh [registry]      (run in WSL; needs docker and `az login`; registry defaults to ABOGI_REGISTRY in .env)
# The image is tagged with the current commit. `az acr build` cannot read this
# Dockerfile (its --platform FROM line), so the image is built here and pushed.
set -euo pipefail
cd "$(dirname "$0")/.."
registry="${1:-$(grep -E "^ABOGI_REGISTRY=" .env | cut -d= -f2- || true)}"
if [ -z "$registry" ]; then
  echo "Name the container registry: scripts/deploy.sh <registry>, or set ABOGI_REGISTRY in .env." >&2
  exit 1
fi
image="$registry.azurecr.io/abogi:$(git rev-parse --short HEAD)"
if [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes: the image would not match commit $(git rev-parse --short HEAD). Commit first." >&2
  exit 1
fi
docker build -q -t "$image" .
az acr login -n "$registry"
docker push -q "$image"
uv run --env-file .env python scripts/deploy_agent.py "$image"
