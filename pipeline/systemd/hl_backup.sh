#!/usr/bin/env bash
# Nightly GCS backup of the recoverable state (plan §7.7, R19): ledger
# backups, dossiers, reports -> gs://$HL_BACKUP_BUCKET. `rclone copy`,
# NEVER `sync`: this bucket is the disaster-recovery copy, and a sync
# from a fresh/empty local dir (e.g. right after a VM recreate) would
# DELETE the very data you would be recovering from (review-r1 #5).
# Copy is append/overwrite-only; the 14-day local backup rotation keeps
# growth bounded. On ANY failure: inline Telegram alert (telegram.py has
# no CLI entry point, hence the uv one-liner), then exit 1 so systemd
# records the failure. Untemplated on purpose — reads $HOME and
# $HL_BACKUP_BUCKET from the service unit's User=/Environment=.
set -u
ok=1
for d in backups dossiers reports; do
  mkdir -p "$HOME/hl-pipeline/$d"
  rclone copy "$HOME/hl-pipeline/$d" "gcs-backup:${HL_BACKUP_BUCKET}/$d" || ok=0
done
if [ "$ok" -ne 1 ]; then
  cd "$HOME/hl-gamedata"
  "$HOME/.local/bin/uv" run python -c \
    "from pipeline import config, telegram; telegram.send_message(config.load(), '⚠️ GCS backup failed')" \
    || true
  exit 1
fi
