"""Minimal Basecamp 4 API client (stdlib only)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from . import auth
from .config import Config



class ApiError(RuntimeError):
    pass


def resolve_token(cfg: Config) -> str:
    """Token sources, first match wins:
    1. env var named by `token_env` (raw token, no refresh)
    2. bcbot's own OAuth credentials (`bcbot auth login`), auto-refreshed
    3. the official Basecamp CLI's stored login for `profile`, auto-refreshed
    """
    if cfg.token_env and os.environ.get(cfg.token_env):
        return os.environ[cfg.token_env]
    tok = auth.stored_token(cfg.credentials)
    if tok:
        return tok
    exe = shutil.which("basecamp")
    if not exe:
        raise ApiError("no Basecamp credentials: run `bcbot auth login` "
                       f"(or set ${cfg.token_env}, or install/log in the basecamp CLI)")
    cmd = [exe] + (["--profile", cfg.profile] if cfg.profile else []) + ["auth", "token", "--stored"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    token = out.stdout.strip()
    if out.returncode != 0 or not token:
        raise ApiError(f"basecamp auth token failed: {out.stderr.strip()[:300]}")
    return token


class Client:
    def __init__(self, cfg: Config, token: str | None = None):
        self.base = f"https://3.basecampapi.com/{cfg.account_id}"
        self._cfg = cfg
        self._token = token
        # Basecamp asks every integration to identify itself with an app name and contact.
        self.user_agent = cfg.user_agent or "basecamp-bot"

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = resolve_token(self._cfg)
        return self._token

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"

    def request(self, method: str, path: str, body: dict | None = None):
        url = self._url(path)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return (json.loads(raw) if raw else None), resp.headers
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(int(e.headers.get("Retry-After") or 2 ** attempt), 30))
                    continue
                raise ApiError(f"{method} {url} -> {e.code}: {e.read()[:300]!r}") from None
        raise ApiError(f"{method} {url} kept failing after retries")

    def get(self, path: str):
        return self.request("GET", path)[0]

    def get_all(self, path: str, limit_pages: int = 5) -> list:
        """Follow Link: rel=next pagination, capped so a busy project can't stall a tick."""
        items, url = [], path
        for _ in range(limit_pages):
            data, headers = self.request("GET", url)
            items.extend(data or [])
            m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", "") or "")
            if not m:
                break
            url = m.group(1)
        return items

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)[0]

    # --- Domain helpers -------------------------------------------------

    def me(self) -> dict:
        return self.get("my/profile.json")

    def readings(self) -> dict:
        return self.get("my/readings.json")

    def recording(self, bucket: int, rec_id: int) -> dict:
        """Any recording, with its body. The generic endpoint omits `content`, so follow
        its typed `url` (comments/…, card_tables/cards/…) to get the full object."""
        rec = self.get(f"buckets/{bucket}/recordings/{rec_id}.json")
        return self.get(rec["url"]) if rec.get("url") else rec

    def comments(self, bucket: int, rec_id: int) -> list:
        return self.get_all(f"buckets/{bucket}/recordings/{rec_id}/comments.json")

    def people(self, bucket: int | None = None) -> list:
        return self.get_all(f"projects/{bucket}/people.json" if bucket else "people.json")

    def projects(self) -> list:
        return self.get_all("projects.json")

    def chats(self) -> list:
        return self.get_all("chats.json")

    def circle(self, bucket: int) -> dict:
        return self.get(f"/circles/{bucket}.json")

    def chat_lines(self, bucket: int, chat: int) -> list:
        return self.get_all(f"buckets/{bucket}/chats/{chat}/lines.json", limit_pages=2)

    def project(self, bucket: int) -> dict:
        return self.get(f"projects/{bucket}.json")

    def recent_recordings(self, kind: str, buckets: list[int]) -> list:
        ids = ",".join(str(b) for b in buckets)
        return self.get_all(
            f"projects/recordings.json?type={kind}&bucket={ids}&sort=created_at&direction=desc",
            limit_pages=1,
        )

    def post_comment(self, bucket: int, rec_id: int, html: str) -> dict:
        return self.post(f"buckets/{bucket}/recordings/{rec_id}/comments.json", {"content": html})

    def post_chat_line(self, bucket: int, chat: int, text: str) -> dict:
        return self.post(f"buckets/{bucket}/chats/{chat}/lines.json", {"content": text})
