#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

for d in out/*/05-28-26/*/; do
  if [[ -f "$d/rrd_creation.py" && -f "$d/frames.csv" && -f "$d/video.mp4" ]]; then
    echo "[rrd] $d"
    (cd "$d" && python3 rrd_creation.py --session-dir .)
  fi
done
