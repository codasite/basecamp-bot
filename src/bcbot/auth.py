"""Basecamp (37signals Launchpad) OAuth 2 for the user the bot acts as.

Flow: register an integration at https://launchpad.37signals.com/integrations, then
`bcbot auth login` *while signed in to Basecamp as the bot's user*. Tokens last two
weeks; the refresh token is used automatically.
"""

from __future__ import annotations

import http.server
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

LAUNCHPAD = "https://launchpad.37signals.com"
DEFAULT_CREDS = Path(os.environ.get("BCBOT_CREDENTIALS", "~/.config/basecamp-bot/credentials.json")).expanduser()


def authorize_url(client_id: str, redirect_uri: str) -> str:
    q = urllib.parse.urlencode({"type": "web_server", "client_id": client_id, "redirect_uri": redirect_uri})
    return f"{LAUNCHPAD}/authorization/new?{q}"


def _token_request(params: dict) -> dict:
    url = f"{LAUNCHPAD}/authorization/token?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=b"", method="POST", headers={"User-Agent": "basecamp-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, path: Path = DEFAULT_CREDS) -> dict:
    tok = _token_request({"type": "web_server", "client_id": client_id, "client_secret": client_secret,
                          "redirect_uri": redirect_uri, "code": code})
    data = {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri,
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": int(time.time()) + int(tok.get("expires_in", 1209600))}
    _save(path, data)
    return data


def stored_token(path: Path = DEFAULT_CREDS) -> str | None:
    """Return a valid access token from the credentials file, refreshing if near expiry."""
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("expires_at", 0) - time.time() > 3600:
        return data["access_token"]
    if not data.get("refresh_token"):
        return data.get("access_token")
    tok = _token_request({"type": "refresh", "refresh_token": data["refresh_token"],
                          "client_id": data["client_id"], "client_secret": data["client_secret"],
                          "redirect_uri": data["redirect_uri"]})
    data["access_token"] = tok["access_token"]
    data["expires_at"] = int(time.time()) + int(tok.get("expires_in", 1209600))
    _save(path, data)
    return data["access_token"]


def wait_for_code(redirect_uri: str, timeout: int = 300) -> str | None:
    """If the redirect URI is on localhost, catch the ?code= callback ourselves."""
    u = urllib.parse.urlparse(redirect_uri)
    if u.hostname not in ("localhost", "127.0.0.1"):
        return None
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"] = (q.get("code") or [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"basecamp-bot: authorized, you can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer((u.hostname, u.port or 80), H)
    srv.timeout = timeout
    srv.handle_request()
    return got.get("code")


def launchpad_identity(token: str) -> dict:
    req = urllib.request.Request(f"{LAUNCHPAD}/authorization.json",
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": "basecamp-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())
