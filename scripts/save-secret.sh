#!/usr/bin/env bash
# Save a token from the Windows clipboard without showing it, then clear the clipboard.
#   scripts/save-secret.sh github     -> Key Vault secret "github-token"
#   scripts/save-secret.sh discord    -> ABOGI_DISCORD_TOKEN in .env (git-ignored) and
#                                        Key Vault secret "discord-token" (for the hosted agent)
#   scripts/save-secret.sh discord-vault -> copy the token already in .env to Key Vault
# Run in WSL, right after pressing the site's own Copy button: sites show a token once.
set -euo pipefail
cd "$(dirname "$0")/.."
vault="$(grep -E '^ABOGI_(GITHUB_TOKEN|PROFILE|GOOGLE_TOKEN)_VAULT=' .env | head -1 | cut -d= -f2- | sed -E 's#https://([^.]+)\..*#\1#')"
to_vault() {  # to_vault <secret name> <value>
  az keyvault secret set --vault-name "$vault" --name "$1" --value "$2" --query name -o tsv >/dev/null
  echo "Saved to Key Vault $vault as $1."
}
if [ "${1:-}" = discord-vault ]; then
  token="$(grep -E '^ABOGI_DISCORD_TOKEN=' .env | head -1 | cut -d= -f2-)"
  [ -n "$token" ] || { echo "No ABOGI_DISCORD_TOKEN in .env. Nothing saved."; exit 1; }
  to_vault discord-token "$token"
  exit 0
fi
token="$(powershell.exe -NoProfile -Command Get-Clipboard | tr -d '\r\n')"
case "${1:-}" in
  github)
    case "$token" in github_pat_*) ;; *) echo "The clipboard does not hold a GitHub token (github_pat_...). Nothing saved."; exit 1 ;; esac
    to_vault github-token "$token"
    ;;
  discord)
    if ! printf '%s' "$token" | grep -Eq '^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}$'; then
      echo "The clipboard does not hold a Discord bot token. Nothing saved."; exit 1
    fi
    sed -i '/^ABOGI_DISCORD_TOKEN=/d' .env
    printf 'ABOGI_DISCORD_TOKEN=%s\n' "$token" >> .env
    echo "Saved to .env as ABOGI_DISCORD_TOKEN."
    to_vault discord-token "$token"
    ;;
  *) echo "Usage: scripts/save-secret.sh github|discord|discord-vault"; exit 2 ;;
esac
powershell.exe -NoProfile -Command 'Set-Clipboard -Value $null'
echo "Clipboard cleared."
