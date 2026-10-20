#!/usr/bin/env bash
# Install lab/sync.sh as a job of this Mac, run each morning at 07:15 (and at wake, if it slept
# then). Yours to run, once:
#
#   lab/launchd/install.sh            to install it, or again after the checkout moved
#   lab/launchd/install.sh --remove   to remove it
set -euo pipefail
cd "$(dirname "$0")/../.."
checkout="$(pwd)"
agent="$HOME/Library/LaunchAgents/com.wata.lab.sync.plist"
launchctl bootout "gui/$(id -u)" "$agent" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$agent"
  echo "Removed."
  exit 0
fi
mkdir -p "$HOME/Library/LaunchAgents" var/lib/lab/log
sed "s#__CHECKOUT__#$checkout#g" lab/launchd/com.wata.lab.sync.plist > "$agent"
launchctl bootstrap "gui/$(id -u)" "$agent"
echo "Installed: lab/sync.sh runs each morning at 07:15; its output goes to var/lib/lab/log/sync.log."
