#!/usr/bin/env bash
# The flip's ARMING GATE. Use this, never a bare `pytest ... ; echo $?`.
#
# "Suite green" cannot mean "exit status 0". run_continuous ends its finally
# with os._exit(0) when it owns the process, and ~12 tests call it in-process:
# revert the install_signals guard and the interpreter dies mid-suite with
# status 0, no summary line, and most tests never run. Measured on this repo:
# 140 of 439 tests ran, nothing was printed, exit 0 (r-loop 6). A pytest
# hook cannot defend against that — os._exit skips every hook, including
# pytest_sessionfinish — so the check has to live OUT here, in the parent.
#
# Two things must both hold: a summary line must EXIST, and the number of
# passing tests must be at least SUITE_FLOOR. Raise the floor when the suite
# grows; never lower it to make a red run green.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SUITE_FLOOR="${SUITE_FLOOR:-670}"
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

set +e
PYTHONPATH=. "${UV:-uv}" run --with pytest "$@" \
    pytest pipeline/tests translator/tests -q 2>&1 | tee "$OUT"
rc="${PIPESTATUS[0]}"
set -e

summary="$(grep -oE '[0-9]+ passed' "$OUT" | tail -1 || true)"
if [ -z "$summary" ]; then
    echo "FATAL: no pytest summary line — the suite did NOT finish." >&2
    echo "  A truncated run and a green run are indistinguishable by exit" >&2
    echo "  status alone; that is exactly what this gate exists to catch." >&2
    exit 1
fi
passed="${summary% passed}"
if [ "$passed" -lt "$SUITE_FLOOR" ]; then
    echo "FATAL: only $passed tests passed, floor is $SUITE_FLOOR." >&2
    echo "  Either the suite was truncated or tests vanished. Do NOT arm." >&2
    exit 1
fi
if [ "$rc" -ne 0 ]; then
    echo "FATAL: pytest exited $rc" >&2
    exit "$rc"
fi
echo "ARMING GATE OK: $passed passed (floor $SUITE_FLOOR)"
