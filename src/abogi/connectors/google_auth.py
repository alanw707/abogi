"""The owner's Google sign-in for connectors that talk to Google directly.

Run once: `uv run python -m abogi.connectors.google_auth`. It opens Google's
consent page, catches the redirect on a temporary localhost port, and saves a
refresh token next to the OAuth client file (both outside the repo, mode 600).

After that, `google_token()` gives connectors a bearer-token provider that
refreshes the short-lived access token itself. While the Google app is in
"Testing" mode, Google expires the refresh token after 7 days; signing in
again fixes it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import anyio

from abogi.connectors.mcp_connector import TokenProvider

# Read-only calendar scopes, as listed in Google's Calendar MCP server guide.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]

_CONFIG = Path.home() / ".config" / "abogi"


def client_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("ABOGI_GOOGLE_CLIENT_FILE") or _CONFIG / "google-oauth-client.json")


def token_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("ABOGI_GOOGLE_TOKEN_FILE") or _CONFIG / "google-token.json")


def google_token(path: Path) -> TokenProvider:
    """A provider backed by the sign-in saved on this machine."""
    return _refreshing(lambda: json.loads(path.read_text()))


def google_token_from_vault(vault_url: str, secret_name: str) -> TokenProvider:
    """A provider backed by the sign-in stored in Azure Key Vault (hosted agent).

    The secret is read on first use with the ambient Azure identity (the
    agent's managed identity when hosted), then kept in memory.
    """

    def load() -> dict:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        return json.loads(client.get_secret(secret_name).value)

    return _refreshing(load)


def _refreshing(load: Callable[[], dict]) -> TokenProvider:
    """Load the saved sign-in once, then hand out fresh access tokens."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials: list[Credentials] = []

    from google.auth.exceptions import RefreshError

    def reload() -> Credentials:
        credentials[:] = [Credentials.from_authorized_user_info(load(), SCOPES)]
        return credentials[0]

    def fresh() -> str:
        current = credentials[0] if credentials else reload()
        if current.valid:
            return current.token
        try:
            current.refresh(Request())
        except RefreshError:
            # The owner may have signed in again since we loaded: reload once and retry.
            current = reload()
            current.refresh(Request())
        return current.token

    async def provider() -> str:
        return await anyio.to_thread.run_sync(fresh)

    return provider


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)


def _open_in_windows_browser(url: str) -> bool:
    """WSL has no browser of its own; hand the URL to Windows if we can."""
    try:
        quoted = url.replace("'", "''")
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{quoted}'"],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _prefer_windows_browser() -> None:
    """Let `webbrowser` (used by the OAuth library) open URLs in Windows from WSL."""
    import shutil
    import webbrowser

    if not shutil.which("powershell.exe"):
        return

    class WindowsBrowser(webbrowser.BaseBrowser):
        def open(self, url: str, new: int = 0, autoraise: bool = True) -> bool:
            return _open_in_windows_browser(url)

    webbrowser.register("windows", None, WindowsBrowser("windows"), preferred=True)


def sign_in(env: Mapping[str, str] | None = None) -> Path:
    """Run Google's installed-app sign-in and save the refresh token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    source = client_file(env)
    if not source.exists():
        sys.exit(f"No OAuth client file at {source}. Create the Desktop client first.")
    flow = InstalledAppFlow.from_client_secrets_file(str(source), SCOPES)
    _prefer_windows_browser()
    credentials = flow.run_local_server(
        host="localhost",
        port=8765,
        open_browser=True,
        # Printed too, in case no browser opens: "{url}" is filled in by the library.
        authorization_prompt_message="If no browser opened, visit this URL to sign in:\n{url}",
        access_type="offline",
        prompt="consent",
    )
    target = token_file(env)
    _write_private(target, credentials.to_json())
    return target


def upload_to_vault(path: Path, vault_url: str, secret_name: str = "google-token") -> None:
    """Store the saved sign-in in Key Vault for the hosted agent, without printing it."""
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    client.set_secret(secret_name, path.read_text(), content_type="application/json")


def main(argv: list[str] | None = None) -> None:
    """Sign in; with ABOGI_GOOGLE_TOKEN_VAULT set (or --vault URL), also update Key Vault.

    The hosted agent re-reads the vault copy when Google rejects its cached
    refresh token, so renewing needs no redeploy.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m abogi.connectors.google_auth")
    parser.add_argument("--vault", default=os.environ.get("ABOGI_GOOGLE_TOKEN_VAULT", ""))
    parser.add_argument("--secret", default=os.environ.get("ABOGI_GOOGLE_TOKEN_SECRET") or "google-token")
    args = parser.parse_args(argv)

    saved = sign_in()
    print(f"Saved Google token to {saved}")
    if args.vault:
        upload_to_vault(saved, args.vault, args.secret)
        print(f"Updated secret {args.secret} in {args.vault}")


if __name__ == "__main__":
    main()
