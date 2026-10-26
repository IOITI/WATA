#!/bin/sh
# The dumps of WATA's database, made by the pg_backup1 container (docker-compose.yml) into
# var/lib/backups, which lab/sync.sh pulls to the Mac each morning, and sent off the server
# encrypted when .env says where.
#
#   pg_backup.sh          each night at BACKUP_AT (UTC, the markets closed): a dump without the bars'
#                         rows, and on Sundays a whole one; the last BACKUP_KEEP nightly and
#                         BACKUP_KEEP_WEEKS whole ones are kept
#   pg_backup.sh now      one dump now, as the night's (before a risky change, say), then exit
#   pg_backup.sh check    restore the newest dump into a scratch database, count the rows of the
#                         trading tables, drop it: a backup is only one once it was restored
#   pg_backup.sh offsite  the copies kept off the server, by folder
#
# On the server: docker exec pg_backup1 sh /pg_backup.sh check
#
# Off the server (BACKUP_AGE_RECIPIENT and BACKUP_S3_BUCKET in .env; BACKUP_OFFSITE, an rclone path,
# in place of the bucket): each dump is encrypted with age to that public key, whose private half
# stays on the Mac, sent with rclone to nightly/ or weekly/ (the Sunday dumps) under the bucket's
# BACKUP_S3_PREFIX, and the copies past BACKUP_OFFSITE_KEEP and BACKUP_OFFSITE_KEEP_WEEKS deleted
# there (0 deletes none). Nothing is sent unencrypted, and nothing here can decrypt: the Mac restores
# the newest copy (python -m lab backup check --offsite). Why a copy failed is in docker logs pg_backup1.
#
# The bars recorded (Saxo's, Capital.com's) are many and only grow; they are in the Sunday dump, and
# day by day in the research export. A dump is written as .part and renamed once complete;
# last.json says what the last night did, its copy off the server included, for the admin's health.
set -eu

BACKUPS="${BACKUPS:-/backups}"
AT="${BACKUP_AT:-02:17}"
KEEP="${BACKUP_KEEP:-14}"
KEEP_WEEKS="${BACKUP_KEEP_WEEKS:-4}"
DATABASE="${PGDATABASE:-wata}"
BARS="price_bar_10s* capital_bar_10s* price_volume*"
TRADING_TABLES="turbo_data_position turbo_data_order signal_journal order_request order_execution trade_risk_state"

OFFSITE="${BACKUP_OFFSITE:-}"
if [ -z "$OFFSITE" ] && [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  OFFSITE="offsite:${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-wata}"
fi
RECIPIENTS="${BACKUP_AGE_RECIPIENT:-}"
OFFSITE_KEEP="${BACKUP_OFFSITE_KEEP:-14}"
OFFSITE_KEEP_WEEKS="${BACKUP_OFFSITE_KEEP_WEEKS:-8}"
# Bounded: a bucket that does not answer must not hold the next night's dump.
RCLONE="rclone --contimeout 60s --timeout 300s --retries 3 --low-level-retries 10"
SEALED="$BACKUPS/.offsite"

number() { echo "$1" | sed 's/^0//'; }

seconds_until() {
  target=$(( $(number "${1%:*}") * 3600 + $(number "${1#*:}") * 60 ))
  now=$(( $(number "$(date -u +%H)") * 3600 + $(number "$(date -u +%M)") * 60 + $(number "$(date -u +%S)") ))
  echo $(( (target - now + 86400) % 86400 ))
}

moment() { date -u +%Y-%m-%dT%H:%M:%SZ; }

said() {
  # last.json: what the last attempt did, read by the admin's health. $1 when it was dumped, $6 what
  # became of its copy off the server (null when none is asked for).
  printf '{"at": "%s", "file": "%s", "bytes": %s, "ok": %s, "detail": "%s", "offsite": %s}\n' \
    "$1" "$2" "$3" "$4" "$5" "$6" > "$BACKUPS/last.json.part"
  mv "$BACKUPS/last.json.part" "$BACKUPS/last.json"
}

offsite_said() {
  # $1 true or false, $2 what happened; with when a copy was last sent (offsite_ok_at), whatever tonight did.
  last_ok="null"
  if [ -s "$BACKUPS/offsite_ok_at" ]; then last_ok="\"$(cat "$BACKUPS/offsite_ok_at")\""; fi
  printf '{"ok": %s, "detail": "%s", "last_ok_at": %s}' "$1" "$2" "$last_ok"
}

send_offsite() {
  # What became of the copy of the dump $1 off the server, as JSON.
  name="$1"
  if [ -z "$OFFSITE" ] && [ -z "$RECIPIENTS" ]; then
    echo null
    return
  fi
  if [ -z "$RECIPIENTS" ]; then
    offsite_said false "No BACKUP_AGE_RECIPIENT in .env: nothing is sent unencrypted"
    return
  fi
  if [ -z "$OFFSITE" ]; then
    offsite_said false "No BACKUP_S3_BUCKET in .env: nowhere to send it"
    return
  fi
  case "$name" in
    *-full.dump) folder="weekly"; keep="$OFFSITE_KEEP_WEEKS" ;;
    *) folder="nightly"; keep="$OFFSITE_KEEP" ;;
  esac
  mkdir -p "$SEALED"
  rm -f "$SEALED"/*
  set --
  for recipient in $RECIPIENTS; do set -- "$@" -r "$recipient"; done
  if ! age "$@" -o "$SEALED/$name.age" "$BACKUPS/$name" >&2; then
    rm -f "$SEALED/$name.age"
    offsite_said false "age could not encrypt $name (docker logs pg_backup1)"
    return
  fi
  # shellcheck disable=SC2086
  if ! $RCLONE copyto "$SEALED/$name.age" "$OFFSITE/$folder/$name.age" >&2; then
    rm -f "$SEALED/$name.age"
    offsite_said false "rclone could not send $name (docker logs pg_backup1)"
    return
  fi
  rm -f "$SEALED/$name.age"
  moment > "$BACKUPS/offsite_ok_at"
  if [ "$keep" -le 0 ]; then
    # 0 deletes nothing there: the bucket's own rules do, for a key that may not delete.
    offsite_said true "Sent to $folder/"
    return
  fi
  # The oldest copies past what is kept there. A failure is said; the copy stays sent.
  # shellcheck disable=SC2086
  if ! listed=$($RCLONE lsf --files-only "$OFFSITE/$folder/"); then
    offsite_said true "Sent to $folder/; the copies there could not be listed, none was deleted"
    return
  fi
  echo "$listed" | grep -E '^wata-[0-9]{4}-[0-9]{2}-[0-9]{2}(-full)?\.dump\.age$' | sort -r | tail -n +$((keep + 1)) |
    while read -r stale; do
      # shellcheck disable=SC2086
      $RCLONE deletefile "$OFFSITE/$folder/$stale" >&2 || echo "Could not delete $folder/$stale" >&2
    done
  offsite_said true "Sent to $folder/"
}

dump() {
  day=$(date -u +%Y-%m-%d)
  at=$(moment)
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
    bytes="$(wc -c < "$BACKUPS/$name" | tr -d ' ')"
    said "$at" "$name" "$bytes" true "dumped" "$(send_offsite "$name")"
  else
    rm -f "$BACKUPS/$name.part"
    if [ -n "$OFFSITE$RECIPIENTS" ]; then offsite="$(offsite_said false "No dump to send")"; else offsite=null; fi
    said "$at" "$name" 0 false "pg_dump failed" "$offsite"
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

listing() {
  if [ -z "$OFFSITE" ]; then
    echo "No copy is sent off the server: BACKUP_S3_BUCKET is not set in .env." >&2
    exit 1
  fi
  for folder in nightly weekly; do
    echo "$folder/:"
    # shellcheck disable=SC2086
    $RCLONE lsl "$OFFSITE/$folder/" | sort -k 4 -r
  done
}

mkdir -p "$BACKUPS"
case "${1:-}" in
  check) check; exit 0 ;;
  now) dump; exit 0 ;;
  offsite) listing; exit 0 ;;
esac

while true; do
  sleep "$(seconds_until "$AT")"
  dump
  # Past the minute: the next sleep is a day long.
  sleep 61
done
