#!/usr/bin/env bash
# Wire this checkout into a Hermes Agent install. Idempotent; only creates symlinks
# and a starter config.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
hermes="${HERMES_HOME:-$HOME/.hermes}"

mkdir -p "$HOME/.local/bin" "$hermes/skills/productivity" "$hermes/scripts" "$HOME/.config/basecamp-bot"
ln -sfn "$repo/bin/bcbot" "$HOME/.local/bin/bcbot"
ln -sfn "$repo/skills/basecamp-bot" "$hermes/skills/productivity/basecamp-bot"
ln -sfn "$repo/integrations/hermes/basecamp_bot_monitor.py" "$hermes/scripts/basecamp_bot_monitor.py"

cfg="$HOME/.config/basecamp-bot/config.toml"
if [[ ! -e "$cfg" ]]; then
  cp "$repo/config.example.toml" "$cfg"
  echo "wrote starter config: $cfg"
fi
echo "installed: bcbot, skill basecamp-bot, monitor script basecamp_bot_monitor.py"
