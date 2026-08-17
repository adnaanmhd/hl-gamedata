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
for u in hl-pipeline.service hl-pipeline.timer hl-backup.service hl-backup.timer hl-pipeline-alert.service hl-backup-alert.service hl-continuous.service hl-continuous-alert.service; do
  sed -e "s|__USER__|$ME|g" -e "s|__HOME__|$HOME|g" -e "s|__BUCKET__|$BUCKET|g" \
    "$UNITS/$u.in" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo systemctl daemon-reload

# --enable-timers no longer arms hl-pipeline.timer (the batch tick): since
# the 2026-08-17 continuous flip that timer is the ROLLBACK path, and a
# post-flip re-provision must never resurrect two drivers on one ledger.
# Arming the batch driver for rollback is an explicit manual act:
#   sudo systemctl enable --now hl-pipeline.timer   (after stopping
#   hl-continuous and setting PIPELINE_CONTINUOUS=False)
if [ "${1:-}" = "--enable-timers" ]; then
  sudo systemctl enable --now hl-backup.timer
  systemctl list-timers hl-backup.timer --no-pager
fi
if [ "${1:-}" = "--enable-continuous" ]; then
  # DISARM the batch timer first: after a rollback (which enables it) the
  # natural roll-forward left BOTH drivers armed, and hl-pipeline.timer's
  # Persistent=true fires a catch-up tick at boot that wins run.lock —
  # hl-continuous then crash-loops to its start limit and stays down while
  # the batch driver silently runs production (r-loop 2)
  sudo systemctl disable --now hl-pipeline.timer || true
  # `disable --now` on the TIMER stops the timer; it does NOT stop an
  # hl-pipeline.service instance a previous elapse already started — and
  # that unit is Type=oneshot with TimeoutStartSec=infinity, so a backlog
  # run legitimately runs for HOURS holding run.lock. hl-continuous would
  # then exit 1 five times, burn StartLimitBurst in ~50s, enter `failed`,
  # and stay down needing a `systemctl reset-failed` that appears nowhere
  # in FLIP_RUNBOOK — while this script exited 0 saying ENABLED and the
  # batch driver kept running production. That is the exact end-state the
  # block above says it exists to prevent (r-loop 3).
  sudo systemctl stop hl-pipeline.service || true
  # wait for the lock to actually clear before arming the new driver
  lock="$HOME/hl-pipeline/run.lock"
  for _ in $(seq 1 60); do
    [ -e "$lock" ] || break
    sleep 1
  done
  if [ -e "$lock" ]; then
    echo "FATAL: $lock still held after 60s — a batch run or a driver is" >&2
    echo "  still live. Stop it and re-run; arming now would crash-loop" >&2
    echo "  hl-continuous into its start limit." >&2
    exit 1
  fi
  sudo systemctl enable --now hl-backup.timer hl-continuous.service
  systemctl list-timers hl-backup.timer --no-pager
  # `status` returns 3 for a non-active unit and the pipe is SIGPIPE-
  # fragile: under `set -euo pipefail` that aborted the script BEFORE the
  # proofs below (r-loop 2). Never let the display command decide the run.
  systemctl status hl-continuous.service --no-pager 2>&1 | head -5 || true
  # boot-persistence proof: the unit must actually be enabled (an absent
  # [Install] section makes enable a no-op and the driver dies on reboot)
  [ "$(systemctl is-enabled hl-continuous.service)" = "enabled" ] \
    || { echo "FATAL: hl-continuous.service not enabled — check [Install]" >&2; exit 1; }
  # ... and the batch timer must be OFF, or two drivers are armed
  batch_state="$(systemctl is-enabled hl-pipeline.timer 2>/dev/null || true)"
  [ "$batch_state" != "enabled" ] \
    || { echo "FATAL: hl-pipeline.timer still enabled — two drivers armed" >&2; exit 1; }
  # is-enabled proves boot persistence, NOT that the thing is running: a
  # unit sitting in `failed` after burning its start limit still reports
  # "enabled". Settle, then assert it is genuinely ACTIVE, so a
  # start-limited driver fails this script instead of passing it (r-loop 3).
  sleep 3
  active_state="$(systemctl is-active hl-continuous.service 2>/dev/null || true)"
  if [ "$active_state" != "active" ]; then
    echo "FATAL: hl-continuous.service is '$active_state', not active." >&2
    echo "  journalctl -u hl-continuous -n 50 --no-pager" >&2
    echo "  If it burned its start limit: systemctl reset-failed hl-continuous.service" >&2
    exit 1
  fi
  echo "hl-continuous enabled AND active; hl-pipeline.timer disarmed ($batch_state)"
fi

# --- acceptance (§7.3) -----------------------------------------------------
echo "--- acceptance"
ffmpeg -version | head -1
rclone version | head -1
"$HOME/.local/bin/uv" --version
# the backup unit is templated with $BUCKET — prove that bucket actually
# exists and is listable NOW, not silently at 03:00 (provision_vm.sh may
# have created a suffixed name if the base was taken; review-r4 #31)
rclone lsd "gcs-backup:$BUCKET" >/dev/null \
  || { echo "FATAL: gcs-backup:$BUCKET not listable — set HL_BACKUP_BUCKET to the bucket provision_vm.sh created" >&2; exit 1; }
echo "backup bucket: gs://$BUCKET listable"
echo "timezone: $(timedatectl show -p Timezone --value)"
case "${1:-}" in
  --enable-timers)     echo "DONE (hl-backup.timer ENABLED; batch timer stays rollback-only)";;
  --enable-continuous) echo "DONE (hl-continuous.service + hl-backup.timer ENABLED)";;
  *)                   echo "DONE (units installed, nothing enabled)";;
esac
