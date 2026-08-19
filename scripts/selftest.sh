#!/bin/bash
# selftest — the verify command for the framework repo itself.
#
# Why this exists: every project the framework drives has a Verify command that proves a
# change didn't break it, but the framework repo had none. Changes to dispatch.sh and
# plan-lint.sh — the two scripts the whole safety model rests on — were shipped unverified,
# and dispatch.sh refuses a build dispatch with no verifier (exit 2), so the framework could
# not even dogfood its own pipeline.
#
# Usage: bash scripts/selftest.sh
# Exit:  0 = all checks pass; 1 = at least one check failed
set -u
cd "$(dirname "$0")/.." || exit 1

FAIL=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; FAIL=1; }

echo "[selftest] shell syntax"
for f in ./*.sh scripts/*.sh; do
  [ -e "$f" ] || continue
  if bash -n "$f" 2>/dev/null; then pass "$f"; else fail "$f (bash -n)"; fi
done

echo "[selftest] python syntax"
for f in scripts/*.py; do
  [ -e "$f" ] || continue
  if python3 -m py_compile "$f" 2>/dev/null; then pass "$f"; else fail "$f (py_compile)"; fi
done
rm -rf scripts/__pycache__

echo "[selftest] plan-lint fixture expectations"
# fixture:expected_exit — 0 clean, 1 STRUCTURAL corruption, 3 vocabulary drift only
FIXTURES="
plan-good.md:0
plan-vocab.md:3
plan-missing-field.md:1
plan-bad-dep.md:1
plan-nested-depends.md:1
plan-bad-escape.md:1
"
for entry in $FIXTURES; do
  [ -n "$entry" ] || continue
  name="${entry%%:*}"; want="${entry##*:}"
  path="tests/fixtures/$name"
  if [ ! -r "$path" ]; then fail "$name (fixture missing)"; continue; fi
  bash scripts/plan-lint.sh "$path" >/dev/null 2>&1; got=$?
  if [ "$got" = "$want" ]; then pass "$name (exit $got)"
  else fail "$name (expected exit $want, got $got)"; fi
done

echo "[selftest] dispatch records lifecycle for an early exit"
# A missing verifier is rejected before backend or network work.  Use a private log directory
# so this assertion neither depends on nor mutates the framework's real telemetry.
LIFECYCLE_TMP="$(mktemp -d)"
printf 'selftest prompt\n' > "$LIFECYCLE_TMP/task-T999.md"
OPENCODE_DISPATCH_LOG_DIR="$LIFECYCLE_TMP/logs" \
  bash dispatch.sh test-model "$LIFECYCLE_TMP" "$LIFECYCLE_TMP/task-T999.md" \
  >/dev/null 2>&1
got=$?
if [ "$got" = 2 ] && python3 - "$LIFECYCLE_TMP/logs/runs.jsonl" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1])]
starts = {r["run_id"] for r in rows if r.get("event") == "run_start"}
finishes = {r["run_id"] for r in rows if r.get("event") == "run_finish" and r.get("exit") == 2}
assert len(starts) == 1 and starts == finishes
PY
then
  pass "early exit has matched run_start/run_finish"
else
  fail "early exit lifecycle record (dispatch exit $got)"
fi
rm -rf "$LIFECYCLE_TMP"

echo "[selftest] plan-lint handles a real live PLAN without crashing"
# Absolute paths on purpose: relative ones do not resolve inside a git worktree, where the
# [ -r ] guard silently turned a skipped check into a pass and gave false confidence.
for live in /Users/tim/Developer/gamedaytastic-pm/PLAN.md /Users/tim/Developer/hometastic-pm/PLAN.md; do
  if [ ! -r "$live" ]; then printf '  skip %s (not present)\n' "$live"; continue; fi
  bash scripts/plan-lint.sh "$live" >/dev/null 2>&1; got=$?
  if [ "$got" -le 3 ]; then pass "$live (exit $got)"; else fail "$live (crashed, exit $got)"; fi
done

echo
if [ "$FAIL" = 0 ]; then echo "[selftest] PASS"; else echo "[selftest] FAIL"; fi
exit "$FAIL"
