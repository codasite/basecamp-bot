"""bcbot: read Basecamp as a configured user, draft replies, post only after a human approves."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import approvals
from . import auth
from . import config as config_mod
from .api import Client
from .sources import Poller
from .state import State
from .text import html_to_text, text_to_html

CARD_MARK = "🏕️ BASECAMP DRAFT"


def _err(*a):
    print(*a, file=sys.stderr)


def _body(args) -> str:
    if getattr(args, "body", None):
        return args.body
    if getattr(args, "body_file", None):
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


# --- shared helpers ------------------------------------------------------------

def render_context(c: Client, it, limit: int = 25) -> str:
    """The thread around an item, as plain text, with the item itself marked."""
    out = [f"PROJECT: {it['project']}", f"LINK: {it['app_url']}", f"SOURCE: {it['source']}", ""]
    if it["reply_kind"] == "chat":
        lines = sorted(c.chat_lines(it["bucket_id"], it["reply_to"]), key=lambda l: l["id"])[-limit:]
        out.append("=== CAMPFIRE (oldest → newest) ===")
        for l in lines:
            mark = "  <<< THIS ONE" if it["key"] == f"chatline:{l['id']}" else ""
            out.append(f"[{l['created_at'][:16]}] {(l.get('creator') or {}).get('name')}: "
                       f"{html_to_text(l.get('content'))}{mark}")
        return "\n".join(out)
    rec = c.recording(it["bucket_id"], it["reply_to"])
    out.append(f"=== {rec.get('type')}: {rec.get('title')} — by {(rec.get('creator') or {}).get('name')} ===")
    mark = "  <<< THIS ONE" if it["key"] == f"recording:{rec['id']}" else ""
    out.append((html_to_text(rec.get("content") or rec.get("description")) or "(no body)") + mark)
    comments = c.comments(it["bucket_id"], it["reply_to"])[-limit:]
    if comments:
        out += ["", "=== COMMENTS (oldest → newest) ==="]
    for cm in comments:
        mark = "  <<< THIS ONE" if it["key"] == f"comment:{cm['id']}" else ""
        out += ["", f"[{cm['created_at'][:16]}] {(cm.get('creator') or {}).get('name')}:{mark}",
                html_to_text(cm.get("content"))]
    return "\n".join(out)


def render_card(cfg, s: State, draft_id: int) -> str:
    d = s.draft(draft_id)
    if not d:
        raise SystemExit(f"bcbot: no draft #{draft_id}")
    it = s.item(d["item_key"])
    where = "Campfire" if it["reply_kind"] == "chat" else "Comment"
    persona = cfg.persona or "bot user"
    return (f"{CARD_MARK} #{d['id']} — {it['project']} · {where} ({it['source']})\n"
            f"From: {it['author']}\nLink: {it['app_url']}\n\n"
            f"THEIR MESSAGE:\n{it['excerpt']}\n\n"
            f"PROPOSED REPLY (as {persona}):\n{d['body']}\n\n"
            f"Reply “send {d['id']}”, “skip {d['id']}”, or “edit {d['id']}: <new text>”.")


def post_draft(cfg, c: Client, s: State, draft_id: int, body: str | None = None,
               force: bool = False, approver: str | None = None) -> str | None:
    d = s.draft(draft_id)
    if not d:
        raise RuntimeError(f"no draft #{draft_id}")
    if d["status"] != "pending":
        raise RuntimeError(f"draft #{draft_id} is {d['status']}, not pending")
    age = datetime.now(timezone.utc) - datetime.fromisoformat(d["created_at"])
    if age > timedelta(hours=cfg.stale_hours) and not force:
        raise RuntimeError(f"draft #{draft_id} is {age.days}d{age.seconds // 3600}h old; "
                           "the thread may have moved on (redraft, or force)")
    text = (body or "").strip() or d["body"]
    if d["reply_kind"] == "chat":
        res = c.post_chat_line(d["bucket_id"], d["reply_to"], text)
    else:
        res = c.post_comment(d["bucket_id"], d["reply_to"], text_to_html(text))
    url = (res or {}).get("app_url")
    s.decide(draft_id, "posted", posted_url=url, body=text if text != d["body"] else None,
             note=f"approved by {approver}" if approver else None)
    return url


def request_approval(cfg, c: Client, s: State, draft_id: int) -> int | None:
    target = cfg.approval.bucket_chat
    if not target:
        return None
    line = c.post_chat_line(*target, render_card(cfg, s, draft_id))
    s.set_card_line(draft_id, line["id"])
    return line["id"]


def _draft_only() -> bool:
    """Scheduled/agent runs that must never post directly set BCBOT_DRAFT_ONLY=1.
    Hermes cron sessions are detected automatically."""
    return (os.environ.get("BCBOT_DRAFT_ONLY") == "1"
            or os.environ.get("HERMES_SESSION_SOURCE", "").lower() == "cron")


# --- commands ------------------------------------------------------------------

def cmd_whoami(cfg, c, s, args):
    me = c.me()
    print(json.dumps({"id": me["id"], "name": me["name"], "email": me.get("email_address"),
                      "account_id": cfg.account_id, "persona": cfg.persona or me["name"],
                      "voice": cfg.voice, "llm": cfg.llm.provider or None,
                      "approval_chat": cfg.approval.chat or None}, indent=1))


def cmd_accounts(cfg, c, s, args):
    ident = auth.launchpad_identity(c.token)
    for a in ident.get("accounts", []):
        if a.get("product") in ("bc3", "bcx", None):
            print(f"{a['id']}\t{a.get('name')}\t{a.get('product')}")


def cmd_projects(cfg, c, s, args):
    for p in c.projects():
        chat = next((t for t in p.get("dock") or [] if t.get("name") == "chat"), None)
        print(f"{p['id']}\t{p['name']}\t" + (f"campfire={chat['app_url']}" if chat else "no campfire"))


def cmd_people(cfg, c, s, args):
    for p in c.people(args.project):
        print(f"{p['id']}\t{p['name']}\t{p.get('email_address', '')}\t{p.get('title') or ''}")


def cmd_chats(cfg, c, s, args):
    for ch in c.chats():
        b = ch.get("bucket") or {}
        print(f"{ch['app_url']}\t{b.get('type')}\t{ch.get('title') or b.get('name')}")


def cmd_poll(cfg, c, s, args):
    counts = Poller(c, s, cfg, backfill=args.backfill, log=_err).run()
    if args.monitor:
        # Byte-stable while nothing changes, so hash-gated schedulers (e.g. Hermes monitor jobs)
        # only wake an agent when the queue changes. No timestamps here.
        for it in s.items("new"):
            excerpt = " ".join((it["excerpt"] or "").split())[:140]
            print(f"{it['key']}\t{it['source']}\t{it['project']}\t{it['author']}: {excerpt}")
    else:
        print(json.dumps({"new": counts, "waiting": len(s.items("new"))}))


def cmd_inbox(cfg, c, s, args):
    rows = _rows(s.items(args.status))
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        print(f"{r['key']}  [{r['source']}] {r['project']} — {r['author']}\n  {r['app_url']}\n  "
              + (r["excerpt"] or "").replace("\n", "\n  ") + "\n")


def cmd_context(cfg, c, s, args):
    it = s.item(args.key)
    if not it:
        sys.exit(f"bcbot: unknown item {args.key}")
    print(render_context(c, it, args.limit))


def cmd_draft(cfg, c, s, args):
    if not s.item(args.key):
        sys.exit(f"bcbot: unknown item {args.key}")
    body = _body(args).strip()
    if not body:
        sys.exit("bcbot: empty draft body")
    draft_id = s.add_draft(args.key, body)
    if args.request:
        request_approval(cfg, c, s, draft_id)
    print(draft_id)


def autodraft(cfg, c: Client, s: State, limit: int = 10) -> list[int]:
    """Let the configured LLM decide reply/ignore for waiting items. Returns new draft ids."""
    from .llm import Drafter, DraftError
    drafter = Drafter(cfg.llm, cfg.persona, cfg.voice, cfg.context)
    made = []
    for it in s.items("new")[:limit]:
        try:
            decision = drafter.decide(dict(it), render_context(c, it))
        except DraftError as e:  # leave the item queued; it is retried next tick
            _err(f"{it['key']}: {e}")
            continue
        if decision.action == "ignore":
            s.set_item_status(it["key"], "ignored", decision.reason)
        else:
            made.append(s.add_draft(it["key"], decision.body))
    return made


def cmd_autodraft(cfg, c, s, args):
    if not cfg.llm.provider:
        sys.exit("bcbot: set [llm].provider to use autodraft (or draft with an external agent)")
    print(json.dumps({"drafted": autodraft(cfg, c, s, args.max)}))


def cmd_request(cfg, c, s, args):
    ids = args.ids or [r["id"] for r in s.unrequested()]
    if not cfg.approval.bucket_chat:
        sys.exit("bcbot: set [approval].chat to a Campfire or Ping URL")
    for i in ids:
        request_approval(cfg, c, s, i)
    print(json.dumps({"requested": ids}))


def cmd_approvals(cfg, c, s, args):
    n = approvals.process(c, s, cfg, lambda i, body, approver=None: post_draft(cfg, c, s, i, body, approver=approver),
                          log=_err)
    print(json.dumps({"decisions": n}))


def cmd_run(cfg, c, s, args):
    """One tick: poll → (autodraft) → post approval cards → act on approvals. Safe to cron every minute."""
    lock = open(cfg.state_db.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _err("bcbot: previous run still going; skipping this tick")
        return
    summary = {"new": Poller(c, s, cfg, log=_err).run()}
    if cfg.llm.provider:
        summary["drafted"] = len(autodraft(cfg, c, s))
    if cfg.approval.bucket_chat:
        pending = [r["id"] for r in s.unrequested()]
        for i in pending:
            request_approval(cfg, c, s, i)
        summary["requested"] = len(pending)
        summary["decisions"] = approvals.process(
            c, s, cfg, lambda i, body, approver=None: post_draft(cfg, c, s, i, body, approver=approver), log=_err)
    print(json.dumps(summary))


def cmd_ignore(cfg, c, s, args):
    for key in args.keys:
        if not s.item(key):
            sys.exit(f"bcbot: unknown item {key}")
        s.set_item_status(key, "ignored", args.reason)
    print(f"ignored {len(args.keys)}")


def cmd_drafts(cfg, c, s, args):
    rows = _rows(s.drafts(args.status))
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"#{r['id']} {r['item_key']} [{r['project']}] → {r['author']}: {r['body'][:80]!r}")


def cmd_card(cfg, c, s, args):
    print(render_card(cfg, s, args.id))


def cmd_approve(cfg, c, s, args):
    # For flows where a human approved somewhere else (an agent chat, a ticket). Scheduled
    # runs must not be able to post on their own, so this refuses in draft-only mode.
    if _draft_only():
        sys.exit("bcbot: refusing to post in draft-only mode; approval must come from a human")
    try:
        url = post_draft(cfg, c, s, args.id, _body(args), force=args.force, approver=args.approver)
    except RuntimeError as e:
        sys.exit(f"bcbot: {e}")
    print(json.dumps({"posted": args.id, "url": url}))


def cmd_reject(cfg, c, s, args):
    d = s.draft(args.id)
    if not d or d["status"] != "pending":
        sys.exit(f"bcbot: no pending draft #{args.id}")
    s.decide(args.id, "rejected", note=args.reason)
    print(f"rejected #{args.id}")


def cmd_auth(cfg_path, args):
    """`auth` runs before a config exists, so it doesn't take the normal (cfg, client, state)."""
    creds = config_mod.Path(args.credentials).expanduser() if args.credentials else auth.DEFAULT_CREDS
    if args.auth_cmd == "url":
        print(auth.authorize_url(args.client_id, args.redirect_uri))
        return
    if args.auth_cmd == "login":
        secret = args.client_secret or os.environ.get("BASECAMP_CLIENT_SECRET")
        if not secret:
            sys.exit("bcbot: pass --client-secret or set BASECAMP_CLIENT_SECRET")
        print("Open this URL while signed in to Basecamp AS THE BOT'S USER, then approve access:\n\n"
              f"  {auth.authorize_url(args.client_id, args.redirect_uri)}\n", file=sys.stderr)
        code = args.code or auth.wait_for_code(args.redirect_uri)
        if not code:
            code = input("Paste the `code` parameter from the URL you were redirected to: ").strip()
        auth.exchange_code(args.client_id, secret, args.redirect_uri, code, creds)
        ident = auth.launchpad_identity(auth.stored_token(creds))
        who = ident.get("identity", {})
        print(json.dumps({"saved": str(creds), "user": f"{who.get('first_name', '')} {who.get('last_name', '')}".strip(),
                          "email": who.get("email_address"),
                          "accounts": [{"id": a["id"], "name": a["name"]} for a in ident.get("accounts", [])]},
                         indent=1))
        return
    if args.auth_cmd == "status":
        tok = auth.stored_token(creds)
        if not tok:
            sys.exit(f"bcbot: no credentials at {creds}")
        who = auth.launchpad_identity(tok).get("identity", {})
        print(json.dumps({"credentials": str(creds), "email": who.get("email_address")}))


def main(argv=None):
    p = argparse.ArgumentParser(prog="bcbot", description=__doc__)
    p.add_argument("--config", help="path to config.toml (default $BCBOT_CONFIG or ~/.config/basecamp-bot/config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("auth", help="log the bot's Basecamp user in (OAuth)")
    asub = sp.add_subparsers(dest="auth_cmd", required=True)
    for name in ("login", "url"):
        a = asub.add_parser(name)
        a.add_argument("--client-id", required=True)
        a.add_argument("--redirect-uri", required=True)
        if name == "login":
            a.add_argument("--client-secret")
            a.add_argument("--code", help="authorization code, if you already have one")
    asub.add_parser("status")
    sp.add_argument("--credentials", help="credentials file (default ~/.config/basecamp-bot/credentials.json)")

    sub.add_parser("whoami", help="show which Basecamp user the bot acts as")
    sub.add_parser("accounts", help="list Basecamp accounts the token can reach (for account_id)")
    sub.add_parser("projects", help="list projects with ids and Campfire URLs")
    sp = sub.add_parser("people", help="list people with ids (for [approval].approvers)")
    sp.add_argument("--project", type=int)
    sub.add_parser("chats", help="list Campfires/Pings with URLs (for [approval].chat)")

    sp = sub.add_parser("run", help="one full tick: poll, autodraft, request approval, act on approvals")
    sp = sub.add_parser("poll", help="fetch new inbound items into the queue")
    sp.add_argument("--backfill", action="store_true", help="queue existing history on first run")
    sp.add_argument("--monitor", action="store_true", help="print the waiting queue (for hash-gated schedulers)")
    sp = sub.add_parser("inbox", help="list queued items")
    sp.add_argument("--status", default="new")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("context", help="print the thread around an item")
    sp.add_argument("key")
    sp.add_argument("--limit", type=int, default=25)
    sp = sub.add_parser("draft", help="stage a reply (body via --body, --body-file or stdin)")
    sp.add_argument("key")
    sp.add_argument("--body")
    sp.add_argument("--body-file")
    sp.add_argument("--request", action="store_true", help="also post the approval card to Basecamp")
    sp = sub.add_parser("autodraft", help="draft waiting items with the configured LLM")
    sp.add_argument("--max", type=int, default=10)
    sp = sub.add_parser("ignore", help="mark items as not needing a reply")
    sp.add_argument("keys", nargs="+")
    sp.add_argument("--reason")
    sp = sub.add_parser("drafts", help="list drafts")
    sp.add_argument("--status", default="pending")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("card", help="print the approval card for a draft")
    sp.add_argument("id", type=int)
    sp = sub.add_parser("request", help="post approval cards to the Basecamp approval chat")
    sp.add_argument("ids", type=int, nargs="*", help="draft ids (default: every pending draft without a card)")
    sub.add_parser("approvals", help="act on send/skip/edit replies in the approval chat")
    sp = sub.add_parser("approve", help="post a pending draft (human approved elsewhere)")
    sp.add_argument("id", type=int)
    sp.add_argument("--body", help="post this edited text instead")
    sp.add_argument("--body-file")
    sp.add_argument("--approver", help="who approved, for the audit trail")
    sp.add_argument("--force", action="store_true", help="post even if the draft is stale")
    sp = sub.add_parser("reject", help="discard a pending draft")
    sp.add_argument("id", type=int)
    sp.add_argument("--reason")

    args = p.parse_args(argv)
    if args.cmd == "auth":
        return cmd_auth(args.config, args)
    cfg = config_mod.load(config_mod.Path(args.config).expanduser() if args.config else None)
    state = State(cfg.state_db)
    client = Client(cfg)
    globals()[f"cmd_{args.cmd}"](cfg, client, state, args)
