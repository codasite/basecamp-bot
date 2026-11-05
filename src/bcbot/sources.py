"""Discover inbound Basecamp activity addressed to (or visible to) the bot's user.

Each source turns API objects into normalized "items" with a stable key and a reply
target. Keys are derived from the recording id, so the same comment reached via a
mention and via project activity is only drafted once.

First contact with any source records a baseline instead of creating items, so turning
the bot on never drafts replies to months of history (use --backfill to override).
"""

from __future__ import annotations

import base64
import re

from .api import Client
from .state import State
from .text import html_to_text

_BUCKET = re.compile(r"/buckets/(\d+)/")
_CHAT = re.compile(r"/buckets/(\d+)/chats/(\d+)")
_CIRCLE = re.compile(r"/circles/(\d+)")


def _rec_id_from_identifier(ident: str | None) -> int | None:
    """readable_identifier is base64("gid://bc3/Recording/<id>")."""
    if not ident:
        return None
    try:
        gid = base64.b64decode(ident + "=" * (-len(ident) % 4)).decode()
    except Exception:
        return None
    m = re.search(r"/Recording/(\d+)$", gid)
    return int(m.group(1)) if m else None


def item_from_recording(rec: dict, source: str, bucket_name: str | None = None) -> dict:
    """Normalize a recording (comment, chat line, message, todo, card…) into an item."""
    rtype = rec.get("type", "")
    parent = rec.get("parent") or {}
    bucket = rec.get("bucket") or {}
    if rtype == "Comment":
        key, kind, target = f"comment:{rec['id']}", "comment", parent.get("id")
    elif rtype.startswith("Chat::Lines"):
        key, kind, target = f"chatline:{rec['id']}", "chat", parent.get("id")
    else:  # mentioned in the body of a message / todo / card / doc: comment on it directly
        key, kind, target = f"recording:{rec['id']}", "comment", rec["id"]
    creator = rec.get("creator") or {}
    return {
        "key": key,
        "source": source,
        "bucket_id": bucket.get("id") or int(_BUCKET.search(rec.get("app_url", "")).group(1)),
        "project": bucket_name or bucket.get("name"),
        "author": creator.get("name"),
        "author_id": creator.get("id"),
        "excerpt": html_to_text(rec.get("content"))[:4000] or rec.get("title") or "",
        "app_url": rec.get("app_url"),
        "reply_kind": kind,
        "reply_to": target,
        "created_at": rec.get("created_at"),
    }


class Poller:
    def __init__(self, client: Client, state: State, cfg, backfill: bool = False, log=print):
        self.c, self.s, self.cfg, self.backfill, self.log = client, state, cfg, backfill, log
        self._me: int | None = None

    @property
    def me(self) -> int:
        if self._me is None:
            cached = self.s.cursor("me")
            self._me = int(cached) if cached else self.c.me()["id"]
            self.s.set_cursor("me", self._me)
        return self._me

    def _take(self, item: dict, seeding: bool) -> int:
        if item["author_id"] == self.me:
            return 0
        status = "baseline" if seeding and not self.backfill else "new"
        return int(self.s.add_item(item, status=status) and status == "new")

    def run(self) -> dict:
        counts: dict[str, int] = {}
        src = self.cfg.sources
        readings = None
        if src.mentions or src.pings:
            readings = self.c.readings()
        if src.mentions:
            counts["mention"] = self.poll_mentions(readings)
        if src.pings:
            counts["ping"] = self.poll_pings(readings)
        if src.campfires:
            counts["campfire"] = self.poll_campfires(src.campfires)
        if src.activity:
            counts["activity"] = self.poll_activity(src.activity)
        return counts

    # --- mentions -------------------------------------------------------

    def poll_mentions(self, readings: dict) -> int:
        seeding = self.s.cursor("seeded:mentions") is None
        n = 0
        for r in (readings.get("unreads") or []) + (readings.get("reads") or []):
            if r.get("type") != "Mention":
                continue
            rec_id = _rec_id_from_identifier(r.get("readable_identifier"))
            m = _BUCKET.search(r.get("app_url", ""))
            if not rec_id or not m:
                continue
            if any(self.s.item(f"{p}:{rec_id}") for p in ("comment", "chatline", "recording")):
                continue
            try:
                rec = self.c.recording(int(m.group(1)), rec_id)
            except Exception as e:  # deleted / no access: skip, don't fail the tick
                self.log(f"mention {rec_id}: {e}")
                continue
            n += self._take(item_from_recording(rec, "mention", r.get("bucket_name")), seeding)
        self.s.set_cursor("seeded:mentions", 1)
        return n

    # --- chats (pings + campfires) -------------------------------------

    def _poll_chat(self, bucket: int, chat: int, source: str, project: str | None) -> int:
        approval = getattr(getattr(self.cfg, "approval", None), "bucket_chat", None)
        if approval and approval[1] == chat:
            return 0  # the approval channel is for humans deciding on drafts, never a reply source
        cur_name = f"chat:{chat}"
        last = int(self.s.cursor(cur_name) or 0)
        lines = sorted(self.c.chat_lines(bucket, chat), key=lambda l: l["id"])
        if not lines:
            return 0
        seeding = last == 0
        n = 0
        for line in lines:
            if line["id"] <= last:
                continue
            line.setdefault("type", "Chat::Lines::Text")
            line.setdefault("parent", {"id": chat})
            line.setdefault("bucket", {"id": bucket, "name": project})
            n += self._take(item_from_recording(line, source, project), seeding)
        self.s.set_cursor(cur_name, lines[-1]["id"])
        return n

    def poll_pings(self, readings: dict) -> int:
        """Pings are Campfires whose bucket is a Circle rather than a Project.

        They're found two ways because /chats.json coverage of pings is not documented:
        the chats index (non-Project buckets) and any reading that links to a chat.
        """
        found: dict[int, tuple[int, str | None]] = {}
        for c in self.c.chats():
            if (c.get("bucket") or {}).get("type") != "Project":
                found[c["id"]] = (c["bucket"]["id"], c.get("title") or c["bucket"].get("name"))
        for r in (readings.get("unreads") or []) + (readings.get("reads") or []):
            if not (r.get("section") == "pings" or "Ping" in (r.get("title") or "")):
                continue
            url = r.get("app_url", "")
            m = _CHAT.search(url)
            if m:
                found.setdefault(int(m.group(2)), (int(m.group(1)), r.get("bucket_name") or "Ping"))
                continue
            # Pings appear in readings as /circles/<bucket>; resolve the circle's chat room.
            c = _CIRCLE.search(url)
            if c:
                try:
                    room = (self.c.circle(int(c.group(1))).get("room_url") or "")
                except Exception as e:  # no access / deleted: skip, don't fail the tick
                    self.log(f"ping circle {c.group(1)}: {e}")
                    continue
                rm = _CHAT.search(room)
                if rm:
                    found.setdefault(int(rm.group(2)), (int(rm.group(1)), r.get("bucket_name") or "Ping"))
        return sum(self._poll_chat(b, chat, "ping", name) for chat, (b, name) in found.items())

    def poll_campfires(self, buckets: list[int]) -> int:
        n = 0
        for b in buckets:
            proj = self.c.project(b)
            for tool in proj.get("dock") or []:
                if tool.get("name") == "chat" and tool.get("enabled", True):
                    n += self._poll_chat(b, tool["id"], "campfire", proj.get("name"))
        return n

    # --- project activity ---------------------------------------------

    def poll_activity(self, buckets: list[int]) -> int:
        n = 0
        for kind in ("Comment", "Message"):
            cur_name = f"activity:{kind}:{','.join(map(str, sorted(buckets)))}"
            last = self.s.cursor(cur_name)
            recs = self.c.recent_recordings(kind, buckets)
            if not recs:
                continue
            for rec in recs:
                if last and rec["created_at"] <= last:
                    continue
                n += self._take(item_from_recording(rec, "activity"), seeding=last is None)
            self.s.set_cursor(cur_name, max(r["created_at"] for r in recs))
        return n
