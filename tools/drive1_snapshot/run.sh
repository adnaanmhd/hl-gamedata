#!/usr/bin/env bash
# Drive I raw-hours snapshot + exhaustive issues list (the drive1-raw-hours-<date>.csv
# series). Read-only against Drive I: one rclone listing + a copy of every session.json.
#   bash tools/drive1_snapshot/run.sh [YYYY-MM-DD] [previous-csv-name]
# Writes drive1-raw-hours-<date>.csv + drive1-issues-<date>.md into the repo root.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
DATE="${1:-$(date -u +%Y-%m-%d)}"
export DRIVE1_PREV_CSV="${2:-$(ls "$REPO"/drive1-raw-hours-*.csv | grep -v "$DATE" | sort | tail -1 | xargs basename)}"
export DRIVE1_WORKDIR="${DRIVE1_WORKDIR:-${TMPDIR:-/tmp}/drive1-snapshot-$DATE}"
mkdir -p "$DRIVE1_WORKDIR/session_jsons"
echo "workdir=$DRIVE1_WORKDIR  prev=$DRIVE1_PREV_CSV"
rclone lsjson -R --hash --drive-use-created-date drive-collect: > "$DRIVE1_WORKDIR/drive1_listing.json"
date -u +%Y-%m-%dT%H:%M:%SZ > "$DRIVE1_WORKDIR/listing_finished_utc.txt"
# default Drive pacer is ~1 file/s for thousands of tiny files; this is ~20x faster
rclone copy drive-collect: "$DRIVE1_WORKDIR/session_jsons" --include "session.json" --checksum --fast-list \
    --transfers 32 --checkers 32 --drive-pacer-min-sleep 10ms --drive-pacer-burst 200 --retries 5 --low-level-retries 20
cd "$REPO"
PYTHONPATH=. python3 "$HERE/build_drive1_report.py" "$DATE"
python3 "$HERE/render_issues_md.py" "$DATE"
