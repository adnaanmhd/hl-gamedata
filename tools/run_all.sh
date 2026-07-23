#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

run_one() {
  local game="$1" session="$2" inputs_name="$3"
  local in="humynlabs/${game}/05-28-26/${session}"
  local out="out/${game}/05-28-26/${session}"
  echo "[start] ${game}/${session}"
  python3 tools/process_bundle.py \
    --session-dir "${in}" \
    --inputs-jsonl "${in}/${inputs_name}" \
    --output-dir "${out}" \
    > "out/${session}.report.json"
  echo "[done]  ${game}/${session}"
}

# Args: game, session-id, inputs.jsonl basename
run_one kamla       2026-05-27T13-18-20Z_kamla_c_c944bee0e87b2625        inputs.jsonl
run_one kamla       2026-05-27T13-44-46Z_kamla_c_c944bee0e87b2625        inputs.jsonl
run_one outer_wilds 2026-05-27T12-55-40Z_outer_wilds_c_e7c7aa4d6e4b6618  inputs.jsonl
run_one outer_wilds 2026-05-27T13-08-32Z_outer_wilds_c_e7c7aa4d6e4b6618  "inputs (1).jsonl"
run_one outer_wilds 2026-05-27T13-21-33Z_outer_wilds_c_e7c7aa4d6e4b6618  inputs.jsonl
