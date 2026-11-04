# Scheduling `bcbot run`

`bcbot run` does one tick: poll Basecamp, draft (if `[llm]` is set), post approval cards, and
act on approvers' replies. A file lock stops overlapping ticks, so run it every minute.

Put provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
`API_SERVER_KEY`) in the scheduler's environment, never in the config file.

## cron (Linux/macOS)

```cron
* * * * * . $HOME/.config/basecamp-bot/env && /path/to/bcbot run >> $HOME/.local/state/bcbot.log 2>&1
```

`~/.config/basecamp-bot/env` (chmod 600): `export ANTHROPIC_API_KEY=...`

## systemd (user timer)

`~/.config/systemd/user/bcbot.service`
```ini
[Unit]
Description=basecamp-bot tick
[Service]
Type=oneshot
EnvironmentFile=%h/.config/basecamp-bot/env.systemd
ExecStart=%h/.local/bin/bcbot run
```

`~/.config/systemd/user/bcbot.timer`
```ini
[Unit]
Description=basecamp-bot every minute
[Timer]
OnCalendar=*-*-* *:*:00
Persistent=true
[Install]
WantedBy=timers.target
```

`env.systemd` uses `KEY=value` lines (no `export`). Enable: `systemctl --user enable --now bcbot.timer`,
plus `loginctl enable-linger $USER` on servers so it runs while you're logged out.

## launchd (macOS)

`~/Library/LaunchAgents/com.example.bcbot.plist`, with `StartInterval` set to `60`,
`ProgramArguments` set to `["/path/to/bcbot", "run"]`, and the keys under `EnvironmentVariables`.

## Hermes Agent

Two options:

1. **Built-in LLM, Hermes just schedules it:** a script-only job,
   `hermes cron create --script <wrapper that runs bcbot run> --no-agent "every 1m"`.
2. **Hermes drafts:** run `integrations/hermes/install.sh`, leave `[llm].provider` empty, and
   create a monitor job that wakes the agent only when the queue changes:

   ```sh
   hermes cron create --name "Basecamp bot drafts" \
     --monitor-script basecamp_bot_monitor.py --skill basecamp-bot --deliver local \
     "every 1m" "Draft replies for the waiting Basecamp items per the basecamp-bot skill, using bcbot draft --request."
   ```

   Also schedule `bcbot approvals` every minute (option 1 style) so approvers' replies get acted on.
   Hermes cron sessions are draft-only automatically: `bcbot approve` refuses to run in them.

Or point `[llm]` at Hermes' API server (`provider = "hermes"`) and use option 1.

## Claude Code / other agents

To have an agent draft on a schedule, run it headless, e.g.
`BCBOT_DRAFT_ONLY=1 claude -p "Follow AGENTS.md › Operating loop"`, from cron, plus
`bcbot approvals` every minute. Most teams find the built-in `[llm]` simpler.
