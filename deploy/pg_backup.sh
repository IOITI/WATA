#!/bin/sh
# The dumps of WATA's database, made by the pg_backup1 container (docker-compose.yml) into
# var/lib/backups, which lab/sync.sh pulls to the Mac each morning.
#
#   pg_backup.sh          each night at BACKUP_AT (UTC, the markets closed): a dump without the bars'
#                         rows, and on Sundays a whole one; the last BACKUP_KEEP nightly and
#                         BACKUP_KEEP_WEEKS whole ones are kept
#   pg_backup.sh now      one dump now, as the night's (before a risky change, say), then exit
#   pg_backup.sh check    restore the newest dump into a scratch database, count the rows of the
#                         trading tables, drop it: a backup is only one once it was restored
#
# On the server: docker exec pg_backup1 sh /pg_backup.sh check
#
# The bars recorded (Saxo's, Capital.com's) are many and only grow; they are in the Sunday dump, and
# day by day in the research export. A dump is written as .part and renamed once complete;
# last.json says what the last night did, for the admin's health.
set -eu

BACKUPS="${BACKUPS:-/backups}"
AT="${BACKUP_AT:-02:17}"
KEEP="${BACKUP_KEEP:-14}"
KEEP_WEEKS="${BACKUP_KEEP_WEEKS:-4}"
DATABASE="${PGDATABASE:-wata}"
BARS="price_bar_10s* capital_bar_10s* price_volume*"
TRADING_TABLES="turbo_data_position turbo_data_order signal_journal order_request order_execution trade_risk_state"

number() { echo "$1" | sed 's/^0//'; }

seconds_until() {
  target=$(( $(number "${1%:*}") * 3600 + $(number "${1#*:}") * 60 ))
  now=$(( $(number "$(date -u +%H)") * 3600 + $(number "$(date -u +%M)") * 60 + $(number "$(date -u +%S)") ))
  echo $(( (target - now + 86400) % 86400 ))
}

said() {
  # last.json: what the last attempt did, read by the admin's health.
  printf '{"at": "%s", "file": "%s", "bytes": %s, "ok": %s, "detail": "%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" "$4" > "$BACKUPS/last.json.part"
  mv "$BACKUPS/last.json.part" "$BACKUPS/last.json"
}

dump() {
  day=$(date -u +%Y-%m-%d)
  if [ "$(date -u +%u)" = 7 ]; then
    name="wata-$day-full.dump"
    excluded=""
  else
    name="wata-$day.dump"
    excluded=""
    for table in $BARS; do excluded="$excluded --exclude-table-data=$table"; done
  fi
  # shellcheck disable=SC2086
  if pg_dump -d "$DATABASE" -Fc $excluded -f "$BACKUPS/$name.part"; then
    mv "$BACKUPS/$name.part" "$BACKUPS/$name"
    said "$name" "$(wc -c < "$BACKUPS/$name" | tr -d ' ')" true "dumped"
  else
    rm -f "$BACKUPS/$name.part"
    said "$name" 0 false "pg_dump failed"
  fi
  ls -1t "$BACKUPS"/wata-*-full.dump 2>/dev/null | tail -n +$((KEEP_WEEKS + 1)) | xargs -r rm -f
  ls -1t "$BACKUPS"/wata-????-??-??.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
}

check() {
  newest=$(ls -1t "$BACKUPS"/wata-*.dump 2>/dev/null | head -n 1)
  if [ -z "$newest" ]; then
    echo "No dump in $BACKUPS yet." >&2
    exit 1
  fi
  scratch="wata_restore_check"
  dropdb --if-exists "$scratch"
  createdb "$scratch"
  trap 'dropdb --if-exists "$scratch"' EXIT
  pg_restore --no-owner -d "$scratch" "$newest"
  echo "Restored $(basename "$newest") into $scratch:"
  for table in $TRADING_TABLES; do
    echo "  $table: $(psql -d "$scratch" -tAc "SELECT count(*) FROM $table" 2>/dev/null || echo 'absent')"
  done
}

mkdir -p "$BACKUPS"
case "${1:-}" in
  check) check; exit 0 ;;
  now) dump; exit 0 ;;
esac

while true; do
  sleep "$(seconds_until "$AT")"
  dump
  # Past the minute: the next sleep is a day long.
  sleep 61
done
