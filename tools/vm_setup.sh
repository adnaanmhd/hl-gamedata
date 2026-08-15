#!/usr/bin/env bash
# Bootstrap the pipeline VM (plan §7.3/§7.4/§7.7). Run ON the VM, from the
# rsynced repo: `bash ~/hl-gamedata/tools/vm_setup.sh [--enable-timers]`.
# Idempotent: safe to re-run after every redeploy rsync.
#
# Installs ffmpeg/sqlite3/rclone/uv, creates ~/hl-pipeline + config dirs,
# writes the three rclone remotes (drive-collect, drive-deliver,
# gcs-backup — same sa.json, F10), sets the VM timezone to IST (Debian
# defaults to UTC — a naive 03:00 OnCalendar would fire at 08:30 IST),
# and templates the systemd units (__USER__/__HOME__/__BUCKET__ — OS Login
# derives usernames from the Google account, so User= cannot be
# hardcoded). Units are installed but NOT enabled: timers arm only at
# step 12 / go-live via --enable-timers.
#
# Prereqs (done from the Mac, §7.4): repo rsynced to ~/hl-gamedata;
# sa.json + secrets.env scp'd to ~/.config/hl-gamedata/ (chmod 600).
# Redeploy from then on: edit+commit on Mac -> same rsync -> next tick.
set -euo pipefail

BUCKET="${HL_BACKUP_BUCKET:-hl-gamedata-pipeline-backups}"
ME="$(id -un)"

sudo timedatectl set-timezone Asia/Kolkata

sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg sqlite3 curl rsync unzip
command -v rclone >/dev/null 2>&1 || curl -fsS https://rclone.org/install.sh | sudo bash
[ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh

mkdir -p "$HOME/hl-pipeline/logs" "$HOME/.config/hl-gamedata" "$HOME/.config/rclone"
# enforce key-file perms regardless of how they were copied (review-r2 #29)
chmod 600 "$HOME/.config/hl-gamedata/"* 2>/dev/null || true

# --- rclone remotes (§7.4): deterministic content, safe to overwrite ------
# drive-collect is scope=drive.readonly: R6 says Drive I is READ-ONLY
# forever — enforce it at the token level, not just by discipline
# (review-r2 #39)
cat > "$HOME/.config/rclone/rclone.conf" <<EOF
[drive-collect]
type = drive
scope = drive.readonly
service_account_file = $HOME/.config/hl-gamedata/sa.json
team_drive = 0AILWuC6lcBKLUk9PVA

[drive-deliver]
type = drive
scope = drive
service_account_file = $HOME/.config/hl-gamedata/sa.json
team_drive = 0AG7V2qXT35aQUk9PVA

[gcs-backup]
type = google cloud storage
service_account_file = $HOME/.config/hl-gamedata/sa.json
bucket_policy_only = true
EOF
chmod 600 "$HOME/.config/rclone/rclone.conf"

# --- systemd units (§7.7): template + install, do NOT enable here ---------
UNITS="$HOME/hl-gamedata/pipeline/systemd"
for u in hl-pipeline.service hl-pipeline.timer hl-backup.service hl-backup.timer hl-pipeline-alert.service hl-backup-alert.service; do
  sed -e "s|__USER__|$ME|g" -e "s|__HOME__|$HOME|g" -e "s|__BUCKET__|$BUCKET|g" \
    "$UNITS/$u.in" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo systemctl daemon-reload

if [ "${1:-}" = "--enable-timers" ]; then
  sudo systemctl enable --now hl-pipeline.timer hl-backup.timer
  systemctl list-timers hl-pipeline.timer hl-backup.timer --no-pager
fi

# --- acceptance (§7.3) -----------------------------------------------------
echo "--- acceptance"
ffmpeg -version | head -1
rclone version | head -1
"$HOME/.local/bin/uv" --version
echo "timezone: $(timedatectl show -p Timezone --value)"
echo "DONE (timers $( [ "${1:-}" = "--enable-timers" ] && echo ENABLED || echo installed, not enabled ))"
