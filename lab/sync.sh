#!/usr/bin/env bash
# Pull what the server writes for research to this machine, then load the new days into the
# lab's database. Over SSH, with your key: run it yourself, or let lab/launchd/install.sh run it
# each morning.
#
#   lab/sync.sh
#
# The server is WATA_SERVER (user@host), else the [wata_server] of the deploy inventory
# (deploy/tools/ansible/inventory/inventory.ini, in this checkout or the main one). Its name is
# never printed. WATA_REMOTE_ROOT is where WATA lives there (/app/wata).
set -euo pipefail
cd "$(dirname "$0")/.."
LAB_ROOT="${WATA_LAB_ROOT:-var/lib/lab}"
REMOTE_ROOT="${WATA_REMOTE_ROOT:-/app/wata}"
PYTHON="${LAB_PYTHON:-.venv-lab/bin/python}"

server="${WATA_SERVER:-}"
if [ -z "$server" ]; then
  main_checkout="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  for inventory in deploy/tools/ansible/inventory/inventory.ini "${main_checkout%/.git}/deploy/tools/ansible/inventory/inventory.ini"; do
    [ -f "$inventory" ] || continue
    server="$(awk '
      /^\[wata_server\]/ { inside = 1; next }
      /^\[/ { inside = 0 }
      inside && NF && $1 !~ /^[#;]/ {
        user = ""
        for (i = 2; i <= NF; i++) if ($i ~ /^ansible_user=/) { split($i, parts, "="); user = parts[2] }
        print (user != "" ? user "@" : "") $1
        exit
      }' "$inventory")"
    [ -n "$server" ] && break
  done
fi
if [ -z "$server" ]; then
  echo "No server to pull from: set WATA_SERVER=user@host, or write the deploy inventory." >&2
  exit 1
fi

mkdir -p "$LAB_ROOT/price_history" "$LAB_ROOT/server_ta" "$LAB_ROOT/log"
summary() { grep -E '^(Number of regular files transferred|Total transferred file size)' || true; }

echo "$(date '+%Y-%m-%d %H:%M:%S') Pulling the research days and the providers' day files"
# A day being written (.part) is left to the next time; the raw hours of Dukascopy stay on the server.
rsync -az --partial --stats \
  --exclude '*.part/' --exclude '*.part' \
  --include 'research/***' \
  --include 'dukascopy/' --include 'dukascopy/instruments.json' --include 'dukascopy/bars/***' \
  --include 'capital/' --include 'capital/bars/***' \
  --exclude '*' \
  "$server:$REMOTE_ROOT/var/lib/price_history/" "$LAB_ROOT/price_history/" | summary

echo "Pulling the database's dumps (the server keeps 14 nights and 4 Sundays; so does this machine)"
mkdir -p "$LAB_ROOT/backups"
# .offsite holds a dump being encrypted for the bucket, for minutes.
rsync -az --partial --stats --delete --exclude '*.part' --exclude '.offsite/' \
  "$server:$REMOTE_ROOT/var/lib/backups/" "$LAB_ROOT/backups/" | summary

echo "Pulling the Pine scripts and the signal rules"
rsync -az --partial --stats "$server:$REMOTE_ROOT/var/lib/admin/ta/" "$LAB_ROOT/server_ta/" | summary

"$PYTHON" -m lab db load
"$PYTHON" -m lab script import
