# basecamp-bot

An assistant for your Basecamp. It **reads as a Basecamp user you choose** (@mentions, Pings,
Campfires, project activity), **drafts replies** with the AI of your choice, and **posts them only
after someone on your team approves them, right inside Basecamp.**

```
            ┌────────────── every minute: bcbot run ───────────────┐
Basecamp ──▶│ read new mentions / pings / campfires / activity      │
            │   → draft a reply (or decide none is needed)           │
            │   → post the draft card in your approval Campfire ────┼──▶ approver: “send 12”
            │   → act on approvers' replies → post as the bot user  │◀── (or skip 12 / edit 12: …)
            └────────────────────────────────────────────────────────┘
```

---

## The easy way: point your AI at this repo

If you use an AI coding agent (Claude Code, Codex, Cursor, Hermes, or similar), open it on a
machine that can run the bot and say:

> Clone https://github.com/codasite/basecamp-bot and set it up for our Basecamp by
> following its AGENTS.md. Walk me through each step.

[`AGENTS.md`](AGENTS.md) tells the agent exactly what to do: install it, help you log the
bot's user in, look up your Basecamp ids, write the config with you, run a live test, and
schedule it. It asks you for decisions, not for ids it can look up itself.

---

## Doing it yourself

### What you'll need

- [ ] A computer or server that's always on, with **Python 3.11+**.
- [ ] A **Basecamp user for the bot**. We recommend a dedicated one, e.g. "Assistant", invited to
      the projects it should see. Replies are posted as this user.
- [ ] **One or more approvers**: the people allowed to release replies.
- [ ] An **approval chat**: a Campfire or Ping where draft cards go. A small private project
      (e.g. "Bot approvals") with just the approvers works well.
- [ ] *(Optional)* An **AI provider key** for built-in drafting: Anthropic (Claude), OpenAI,
      OpenRouter, or a Hermes Agent server. Skip it if your own AI agent will write the drafts.

### 1. Install

```sh
git clone https://github.com/codasite/basecamp-bot && cd basecamp-bot
python3 -m pip install -e '.[all]'
mkdir -p ~/.config/basecamp-bot
cp config.example.toml ~/.config/basecamp-bot/config.toml
```

### 2. Connect the bot's Basecamp user

1. Register an app at <https://launchpad.37signals.com/integrations>, using the redirect URI
   `http://localhost:8765/callback`. Note the **Client ID** and **Client Secret**.
2. Sign in to Basecamp **as the bot user** (a private browser window helps), then run:

   ```sh
   BASECAMP_CLIENT_SECRET=<YOUR_CLIENT_SECRET> \
     bcbot auth login --client-id <YOUR_CLIENT_ID> --redirect-uri http://localhost:8765/callback
   ```

   Open the URL it prints and click approve. bcbot saves and auto-refreshes the login.

### 3. Fill in your details

Look up your ids:

```sh
bcbot accounts    # your Basecamp account id
bcbot projects    # project ids and their Campfire URLs
bcbot people      # person ids (for approvers)
bcbot chats       # Campfire / Ping URLs (for the approval chat)
```

Then edit `~/.config/basecamp-bot/config.toml`:

```toml
account_id = <YOUR_ACCOUNT_ID>
user_agent = "<Your Company> Basecamp Bot (<you@yourcompany.com>)"

persona = "<Name the bot signs as, e.g. Assistant>"
voice   = "<How it should sound, e.g. Friendly, concise, professional.>"
context = """
<Facts it may rely on: hours, services, who handles what.>
"""

[sources]
mentions  = true
pings     = true
campfires = []          # [<PROJECT_ID>, ...] to watch those Campfires
activity  = []          # [<PROJECT_ID>, ...] to consider every new comment/message

[approval]
chat      = "<APPROVAL_CAMPFIRE_OR_PING_URL>"
approvers = [<PERSON_ID>, ...]

[llm]
provider = "claude"     # claude | openai | openrouter | hermes | "" (your own agent drafts)
model    = ""           # required for openai / openrouter
```

Put the AI key in your environment, not in the file: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENROUTER_API_KEY`, or `API_SERVER_KEY` (Hermes).

Check it with `bcbot whoami`.

### 4. Test it

```sh
bcbot poll     # first run records what's already there, so old messages aren't answered
```

1. @mention the bot user in a Basecamp comment.
2. Run `bcbot run`. A draft card appears in your approval chat.
3. As an approver, reply `send <number>` in that chat.
4. Run `bcbot run` again. The reply appears in the thread, and "Posted ✓" appears in the approval chat.

### 5. Run it every minute

```cron
* * * * * . $HOME/.config/basecamp-bot/env && bcbot run >> $HOME/.local/state/bcbot.log 2>&1
```

systemd, launchd and Hermes recipes are in [`docs/scheduling.md`](docs/scheduling.md).

---

## Approving replies

In the approval chat, approvers answer each card:

| Reply | Effect |
|---|---|
| `send 12` (or `ok 12`, `yes 12`) | Post draft #12 as written |
| `skip 12` (or `no 12`) | Discard it |
| `edit 12: <new text>` | Post your text instead |

You can leave out the number when only one draft is waiting. Messages from anyone who isn't a
listed approver are ignored, and so is ordinary conversation like "ok thanks".

## Safety

- Nothing is posted without an approver's reply, and drafts older than `stale_hours` (default 24) are refused.
- The bot ignores its own posts and never treats the approval chat as something to reply to.
- Basecamp content is passed to the AI as untrusted text, and the AI is told not to promise
  dates, prices or decisions.
- Logins are stored at `~/.config/basecamp-bot/credentials.json` with owner-only permissions.

## Using your own AI agent to draft

Leave `[llm].provider` empty. Give your agent [`skills/basecamp-bot/SKILL.md`](skills/basecamp-bot/SKILL.md)
and schedule it with `BCBOT_DRAFT_ONLY=1`. It drafts with `bcbot context` / `bcbot draft --request`,
and `bcbot run` keeps handling approvals. There's a ready-made Hermes setup in `integrations/hermes/`.

## Commands

```
setup     auth login|url|status · accounts · projects · people · chats · whoami
loop      run  (= poll → autodraft → request → approvals)
pieces    poll [--backfill] [--monitor] · inbox · context <key> · autodraft
drafts    draft <key> --body-file f [--request] · drafts · card <id> · request [ids]
decide    approvals · approve <id> [--body-file f] [--force] · reject <id> · ignore <key>…
```

## Development

`PYTHONPATH=src python3 -m unittest discover -s tests`. The code layout and contribution
notes are in [`AGENTS.md`](AGENTS.md).

## License

MIT
