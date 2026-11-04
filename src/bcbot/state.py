"""SQLite state: what we've seen, what's waiting for a draft, and drafts awaiting approval."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  key          TEXT PRIMARY KEY,      -- e.g. comment:123, chatline:456, message:789
  source       TEXT NOT NULL,         -- mention | ping | campfire | activity
  bucket_id    INTEGER NOT NULL,
  project      TEXT,
  author       TEXT,
  author_id    INTEGER,
  excerpt      TEXT,
  app_url      TEXT,
  reply_kind   TEXT NOT NULL,         -- comment | chat
  reply_to     INTEGER NOT NULL,      -- recording id to comment on, or chat id to post a line in
  created_at   TEXT,
  status       TEXT NOT NULL DEFAULT 'new',  -- new | drafted | ignored | replied | baseline
  note         TEXT,
  first_seen   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key    TEXT NOT NULL REFERENCES items(key),
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',  -- pending | posted | rejected | superseded
  created_at  TEXT NOT NULL,
  decided_at  TEXT,
  posted_url  TEXT,
  note        TEXT,
  card_line   INTEGER          -- id of the approval-chat line carrying this draft's card
);
CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(drafts)")}
        if "card_line" not in cols:  # databases created before in-Basecamp approval
            self.db.execute("ALTER TABLE drafts ADD COLUMN card_line INTEGER")

    # cursors
    def cursor(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, name: str, value) -> None:
        self.db.execute("INSERT INTO cursors(name,value) VALUES(?,?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, str(value)))
        self.db.commit()

    # items
    def add_item(self, item: dict, status: str = "new") -> bool:
        """Insert if unseen. Returns True when the item is new to us."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO items(key,source,bucket_id,project,author,author_id,excerpt,app_url,"
            "reply_kind,reply_to,created_at,status,first_seen) "
            "VALUES(:key,:source,:bucket_id,:project,:author,:author_id,:excerpt,:app_url,"
            ":reply_kind,:reply_to,:created_at,:status,:first_seen)",
            {**item, "status": status, "first_seen": now()},
        )
        self.db.commit()
        return cur.rowcount == 1

    def item(self, key: str):
        return self.db.execute("SELECT * FROM items WHERE key=?", (key,)).fetchone()

    def items(self, status: str = "new") -> list:
        return self.db.execute("SELECT * FROM items WHERE status=? ORDER BY created_at, key",
                               (status,)).fetchall()

    def set_item_status(self, key: str, status: str, note: str | None = None) -> None:
        self.db.execute("UPDATE items SET status=?, note=COALESCE(?, note) WHERE key=?", (status, note, key))
        self.db.commit()

    # drafts
    def add_draft(self, key: str, body: str) -> int:
        self.db.execute("UPDATE drafts SET status='superseded', decided_at=? "
                        "WHERE item_key=? AND status='pending'", (now(), key))
        cur = self.db.execute("INSERT INTO drafts(item_key,body,created_at) VALUES(?,?,?)", (key, body, now()))
        self.db.execute("UPDATE items SET status='drafted' WHERE key=?", (key,))
        self.db.commit()
        return cur.lastrowid

    def draft(self, draft_id: int):
        return self.db.execute(
            "SELECT d.*, i.reply_kind, i.reply_to, i.bucket_id, i.app_url, i.project, i.author "
            "FROM drafts d JOIN items i ON i.key=d.item_key WHERE d.id=?", (draft_id,)).fetchone()

    def drafts(self, status: str = "pending") -> list:
        return self.db.execute(
            "SELECT d.*, i.project, i.author, i.app_url FROM drafts d JOIN items i ON i.key=d.item_key "
            "WHERE d.status=? ORDER BY d.id", (status,)).fetchall()

    def set_card_line(self, draft_id: int, line_id: int) -> None:
        self.db.execute("UPDATE drafts SET card_line=? WHERE id=?", (line_id, draft_id))
        self.db.commit()

    def unrequested(self) -> list:
        return self.db.execute("SELECT id FROM drafts WHERE status='pending' AND card_line IS NULL "
                               "ORDER BY id").fetchall()

    def decide(self, draft_id: int, status: str, posted_url: str | None = None, note: str | None = None,
               body: str | None = None) -> None:
        row = self.draft(draft_id)
        self.db.execute("UPDATE drafts SET status=?, decided_at=?, posted_url=?, note=?, body=COALESCE(?, body) "
                        "WHERE id=?", (status, now(), posted_url, note, body, draft_id))
        item_status = {"posted": "replied", "rejected": "ignored"}.get(status)
        if item_status and row:
            self.db.execute("UPDATE items SET status=? WHERE key=?", (item_status, row["item_key"]))
        self.db.commit()
