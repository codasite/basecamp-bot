"""Approval inside Basecamp.

Draft cards are posted (as the bot's user) into one Campfire or Ping — the approval chat.
People listed in [approval].approvers answer there:

    send 12            post draft #12 as written      (also: ok / yes / approve)
    skip 12            discard it                     (also: no / reject)
    edit 12: <text>    post <text> instead

The number can be dropped when exactly one draft is awaiting a decision. Lines from anyone
else, including the bot's own cards, are ignored, so only a listed human can release a post.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import html_to_text

_CMD = re.compile(r"^\s*(send|ok|yes|approve|skip|no|reject|edit)\b\s*#?(\d+)?\s*[:\-–]?\s*(.*)$", re.I | re.S)
_VERB = {"send": "send", "ok": "send", "yes": "send", "approve": "send",
         "skip": "skip", "no": "skip", "reject": "skip", "edit": "edit"}


@dataclass
class Command:
    verb: str          # send | skip | edit
    draft_id: int | None
    text: str = ""


def parse(line: str) -> Command | None:
    m = _CMD.match(line or "")
    if not m:
        return None
    verb = _VERB[m.group(1).lower()]
    text = m.group(3).strip()
    if verb == "edit" and not text:
        return None
    if verb != "edit" and text:
        # "ok thanks for the heads up" is conversation, not an approval.
        return None
    return Command(verb, int(m.group(2)) if m.group(2) else None, text)


def process(client, state, cfg, post_fn, log=print) -> int:
    """Read new approver lines in the approval chat and act on them. Returns actions taken."""
    target = cfg.approval.bucket_chat
    if not target or not cfg.approval.approvers:
        return 0
    bucket, chat = target
    cur = f"approval:{chat}"
    last = int(state.cursor(cur) or 0)
    lines = sorted(client.chat_lines(bucket, chat), key=lambda l: l["id"])
    if not lines:
        return 0
    if last == 0:  # first contact: don't replay old chatter as commands
        state.set_cursor(cur, lines[-1]["id"])
        return 0
    done = 0
    for line in lines:
        if line["id"] <= last:
            continue
        state.set_cursor(cur, line["id"])
        if (line.get("creator") or {}).get("id") not in cfg.approval.approvers:
            continue
        cmd = parse(html_to_text(line.get("content")))
        if not cmd:
            continue
        draft_id = cmd.draft_id
        if draft_id is None:
            waiting = state.drafts("pending")
            if len(waiting) != 1:
                client.post_chat_line(bucket, chat, f"Which draft? {len(waiting)} are waiting — "
                                                    "reply with the number, e.g. “send 12”.")
                continue
            draft_id = waiting[0]["id"]
        d = state.draft(draft_id)
        if not d or d["status"] != "pending":
            client.post_chat_line(bucket, chat, f"Draft #{draft_id} isn't waiting for a decision.")
            continue
        who = (line.get("creator") or {}).get("name")
        try:
            if cmd.verb == "skip":
                state.decide(draft_id, "rejected", note=f"skipped by {who}")
                client.post_chat_line(bucket, chat, f"Skipped #{draft_id}.")
            else:
                url = post_fn(draft_id, cmd.text if cmd.verb == "edit" else None, approver=who)
                client.post_chat_line(bucket, chat, f"Posted #{draft_id} ✓ {url or ''}".strip())
            done += 1
        except Exception as e:  # report in-channel; never retry a post silently
            log(f"approval #{draft_id}: {e}")
            client.post_chat_line(bucket, chat, f"Couldn't post #{draft_id}: {e}")
    return done
