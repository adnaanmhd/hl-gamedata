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
# Run from the repo root regardless of where the operator invoked us. The
# usage line above tells them to call this by ABSOLUTE path (`bash
# ~/hl-gamedata/tools/vm_setup.sh`), i.e. from $HOME, which is where an
# ssh session lands -- but the inline python probes below do
# `sys.path.insert(0, ".")` and so raise ModuleNotFoundError from any
# other cwd. In lock_free() that is indistinguishable from "a live pid
# holds the lock", so --enable-continuous aborted AFTER disabling the
# batch timer and stopping its service: no driver armed at all, with a
# false diagnosis (r-loop 5 blocker). Derived from our own location, not
# hardcoded, so a repo checked out elsewhere still works.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ...and it must be the tree the UNITS actually run. UNITS= below and every
# .in template hardcode $HOME/hl-gamedata (WorkingDirectory=__HOME__/...),
# so arming from any other checkout validates one tree and arms a DIFFERENT
# one: FLIP_RUNBOOK step 5 parks the operator in ~/hl-gamedata-continuous-test
# and steps 6c/6e give the command as a RELATIVE path, so the interlock read
# the side checkout's CONT_DAILY_REPORTS=False and armed a driver running the
# live tree where it is still True — the one precondition whose breach is not
# automatically recoverable (r-loop 6).
LIVE_TREE="$(cd "$HOME/hl-gamedata" 2>/dev/null && pwd || echo "")"
if [ "$PWD" != "$LIVE_TREE" ]; then
  echo "FATAL: running from $PWD, but the systemd units hardcode" >&2
  echo "  $HOME/hl-gamedata as WorkingDirectory — arming from here would" >&2
  echo "  validate this checkout and arm a different one. rsync your" >&2
  echo "  changes to ~/hl-gamedata and re-run from there." >&2
  exit 1
fi

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
  # Wait for the lock to be genuinely FREE — liveness, not existence.
  # run.py installs no SIGTERM handler, so its `finally: release_lock`
  # never runs when `systemctl stop` kills a batch run: the lock DIRECTORY
  # always survives. Testing `[ -e ]` therefore aborted on exactly the case
  # this block exists to handle, and by then the timer was already disabled
  # and the service stopped — leaving NO driver armed at all, with a
  # message ("still live") that was false. The rest of the system treats
  # such a lock as reclaimable: acquire_lock/_pid_is_pipeline pid-reclaim
  # it, and FLIP_RUNBOOK says so in as many words (r-loop 4 blocker,
  # regression from r-loop 3's own wait loop).
  # Ask the code the same question the driver will ask, so this script and
  # the driver can never disagree about whether the lock is held.
  # Exit codes are three-valued on purpose: 0 = free, 1 = genuinely held
  # by a live pid, 2 = the probe itself could not run. Collapsing 2 into
  # 1 is what let a ModuleNotFoundError print "a live pid holds it".
  lock_free() {
    "$HOME/.local/bin/uv" run python - <<'PYEOF' >/dev/null 2>&1
import sys
sys.path.insert(0, ".")
try:
    from pipeline import config, run
    cfg = config.load()
    # acquire/release must be INSIDE the guard too (r-loop 6): a full
    # disk or a permission problem after the step-6b resize makes
    # mkdir(run.lock) raise, and that used to exit 1 -- reported as "a
    # live pid holds it" with the true cause explicitly ruled out.
    held = not run.acquire_lock(cfg)
    if not held:
        run.release_lock(cfg)
except Exception:
    sys.exit(2)                  # probe broken -- NOT "lock is held"
sys.exit(1 if held else 0)
PYEOF
  }
  for _ in $(seq 1 60); do
    if lock_free; then break; fi
    sleep 1
  done
  # `|| rc=$?` both captures the REAL code (a bare `! lock_free` would
  # collapse 2 into 1) and keeps `set -e` from killing the script here.
  lock_rc=0
  lock_free || lock_rc=$?
  if [ "$lock_rc" -ne 0 ]; then
    # ONLY rc 1 means "genuinely held". Everything else -- 2 from the
    # guard, 126/127 from a missing or non-executable uv, whatever uv
    # itself returns -- is a broken probe, about which nothing can be
    # concluded (r-loop 6).
    if [ "$lock_rc" -ne 1 ]; then
      echo "FATAL: could not run the lock-liveness probe at all (rc" >&2
      echo "  $lock_rc from $(pwd)). This is NOT a held lock -- the check" >&2
      echo "  itself is broken, so nothing can be concluded about the" >&2
      echo "  lock. The batch timer is already disabled and its service" >&2
      echo "  stopped, so NO driver is armed: re-arm the previous driver" >&2
      echo "  with 'sudo systemctl enable --now hl-pipeline.timer' or" >&2
      echo "  fix the checkout and re-run." >&2
      exit 1
    fi
    echo "FATAL: run.lock is held by a LIVE pipeline process after 60s." >&2
    echo "  A batch run or a driver is genuinely still running. Stop it" >&2
    echo "  and re-run; arming now would crash-loop hl-continuous into" >&2
    echo "  its start limit. (A stale lock from a killed run is reclaimed" >&2
    echo "  automatically — this message means a live pid holds it.)" >&2
    echo "  Re-arm the previous driver if you are abandoning the roll" >&2
    echo "  forward: sudo systemctl enable --now hl-pipeline.timer" >&2
    exit 1
  fi
  # The PAYMENT-ENDGAME INTERLOCK that stood here (r-loop 4: refuse to arm
  # with CONT_DAILY_REPORTS=True until the 08-15/08-16 regen markers
  # existed in ~/hl-pipeline/reports) is RETIRED by the clean-slate ruling
  # (Adnaan 2026-08-20, FLIP_EXEC_KICKOFF_PROMPT.md): no payment ever went
  # out, the old ledger is archived aside, the regen tooling has nothing to
  # reconcile, and the new era's sheets start fresh from the new ledger's
  # first daily send — so True IS the correct deploy value and a fresh home
  # has no markers by design. The value is still printed so the arming log
  # records what was armed.
  "$HOME/.local/bin/uv" run python -c \
    'import sys; sys.path.insert(0,"."); from pipeline import config as C; print(f"CONT_DAILY_REPORTS={C.CONT_DAILY_REPORTS} (clean-slate era: True is the deploy value)")' \
    || echo "WARN: could not read CONT_DAILY_REPORTS from the checkout" >&2
  # A unit sitting in `failed` after exhausting StartLimitBurst makes
  # `start` return non-zero ("start request repeated too quickly"), and
  # under `set -euo pipefail` that killed the script HERE — before the
  # asserts below and before the reset-failed hint that exists for exactly
  # that state (r-loop 4; same lesson r-loop 2 applied to `systemctl
  # status`). Clear the burnt limit, then let the asserts decide and print.
  sudo systemctl reset-failed hl-continuous.service 2>/dev/null || true
  sudo systemctl enable --now hl-backup.timer hl-continuous.service || true
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
