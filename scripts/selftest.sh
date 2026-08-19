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

echo "[selftest] plan-lint handles a real live PLAN without crashing"
for live in ../gamedaytastic-pm/PLAN.md ../hometastic-pm/PLAN.md; do
  [ -r "$live" ] || continue
  bash scripts/plan-lint.sh "$live" >/dev/null 2>&1; got=$?
  if [ "$got" -le 3 ]; then pass "$live (exit $got)"; else fail "$live (crashed, exit $got)"; fi
done

echo
if [ "$FAIL" = 0 ]; then echo "[selftest] PASS"; else echo "[selftest] FAIL"; fi
exit "$FAIL"
