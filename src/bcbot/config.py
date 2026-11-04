"""Load bot configuration from TOML.

The bot acts as whichever Basecamp user owns the token it is given, so "who am I
impersonating" is a deploy-time setting, not code. See config.example.toml.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG = Path(os.environ.get("BCBOT_CONFIG", "~/.config/basecamp-bot/config.toml")).expanduser()
_CHAT_URL = re.compile(r"/(\d+)/buckets/(\d+)/chats/(\d+)")


@dataclass
class Sources:
    mentions: bool = True
    pings: bool = True
    # Project (bucket) ids whose Campfire is watched line by line.
    campfires: list[int] = field(default_factory=list)
    # Project ids where every new comment and message-board post is a candidate.
    activity: list[int] = field(default_factory=list)


@dataclass
class Approval:
    # Campfire or Ping URL where draft cards are posted and approvers answer.
    chat: str = ""
    # Basecamp person ids allowed to approve. Lines from anyone else are ignored.
    approvers: list[int] = field(default_factory=list)

    @property
    def bucket_chat(self) -> tuple[int, int] | None:
        m = _CHAT_URL.search(self.chat or "")
        return (int(m.group(2)), int(m.group(3))) if m else None


@dataclass
class LLM:
    # claude | openai | openrouter | hermes | "" (no built-in drafting; an external agent drafts)
    provider: str = ""
    model: str = ""
    # Env var holding the key. Defaults per provider (see llm.py).
    api_key_env: str = ""
    # Override the endpoint (OpenAI-compatible providers only).
    base_url: str = ""
    # Claude only: low | medium | high | xhigh | max
    effort: str = "medium"


@dataclass
class Config:
    account_id: int
    profile: str = ""
    # Sent to Basecamp on every request: "<App name> (<contact email or URL>)".
    user_agent: str = ""
    token_env: str = "BASECAMP_TOKEN"
    credentials: Path = Path("~/.config/basecamp-bot/credentials.json").expanduser()
    persona: str = ""
    voice: str = ""
    # Free-form facts about the company the drafter may rely on (hours, services, policies).
    context: str = ""
    state_db: Path = Path("~/.local/share/basecamp-bot/state.db").expanduser()
    stale_hours: int = 24
    sources: Sources = field(default_factory=Sources)
    approval: Approval = field(default_factory=Approval)
    llm: LLM = field(default_factory=LLM)


def _build(cls, raw: dict, where: str):
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise SystemExit(f"bcbot: unknown config keys in {where}: {', '.join(sorted(unknown))}")
    return cls(**raw)


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(f"bcbot: no config at {path} (copy config.example.toml there, or run `bcbot init`)")
    raw = tomllib.loads(path.read_text())
    nested = {"sources": Sources, "approval": Approval, "llm": LLM}
    parts = {k: _build(cls, raw.pop(k, {}) or {}, f"[{k}]") for k, cls in nested.items()}
    for key in ("state_db", "credentials"):
        if key in raw:
            raw[key] = Path(raw[key]).expanduser()
    if "account_id" not in raw:
        raise SystemExit("bcbot: config needs account_id (find it with `bcbot accounts`)")
    return _build(Config, {**raw, **parts}, "config")
