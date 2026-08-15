#!/usr/bin/env bash
# Nightly GCS sync of the recoverable state (plan §7.7, R19): ledger
# backups, dossiers, reports -> gs://$HL_BACKUP_BUCKET. On ANY sync
# failure: inline Telegram alert (telegram.py has no CLI entry point,
# hence the uv one-liner), then exit 1 so systemd records the failure.
# Untemplated on purpose — reads $HOME and $HL_BACKUP_BUCKET from the
# service unit's User=/Environment=.
set -u
ok=1
for d in backups dossiers reports; do
  mkdir -p "$HOME/hl-pipeline/$d"
  rclone sync "$HOME/hl-pipeline/$d" "gcs-backup:${HL_BACKUP_BUCKET}/$d" || ok=0
done
if [ "$ok" -ne 1 ]; then
  cd "$HOME/hl-gamedata"
  "$HOME/.local/bin/uv" run python -c \
    "from pipeline import config, telegram; telegram.send_message(config.load(), '⚠️ GCS backup failed')" \
    || true
  exit 1
fi
