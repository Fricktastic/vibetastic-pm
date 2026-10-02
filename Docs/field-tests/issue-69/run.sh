#!/usr/bin/env bash
# Issue #69 field test: same task, N runs per model, each in its own worktree off `main`.
#
#   bash Docs/field-tests/issue-69/run.sh <pair> [runs]      pair: fast | heavy
#
# fast:  gpt-6-luna      vs gpt-5.6-luna      (both default effort = medium)
# heavy: gpt-6.1-sol@low vs gpt-5.6-sol@low   (explicit @low on both)
#
# Runs alternate between the two models so quota/time-of-day drift hits both equally.
# Candidates are not in MODELS.md, so dispatch.sh reads a temp copy with them appended
# (MODELS_FILE) — the framework's MODELS.md is not changed. No tier is passed (the tier gate
# would reject a non-ladder slug); DISPATCH_ALLOW_NO_TIER=1 is the documented override.
# Results: logs/cost.jsonl rows (prompt ft69-*.md) + results.jsonl from check.py.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../../.." && pwd)"
PAIR="${1:?fast|heavy}"; RUNS="${2:-5}"
case "$PAIR" in
  fast)  MODELS=(gpt-6-luna gpt-5.6-luna) ;;
  heavy) MODELS=(gpt-6.1-sol@low gpt-5.6-sol@low) ;;
  *) echo "pair must be fast or heavy" >&2; exit 2 ;;
esac
OUT="$HERE/out"; mkdir -p "$OUT"
MF="$OUT/MODELS.candidates.md"
{ cat "$REPO/MODELS.md"; printf '\n## Field-test candidates (issue #69, temp copy only)\n\n`gpt-6-luna` `gpt-6.1-sol`\n'; } > "$MF"

for i in $(seq 1 "$RUNS"); do
  for m in "${MODELS[@]}"; do
    tag="$(echo "$m" | tr '@.' '--')-r$i"
    branch="ft69/$tag"
    prompt="$REPO/prompts/ft69-$tag.md"
    cp "$HERE/task.md" "$prompt"
    git -C "$REPO" branch -f "$branch" main >/dev/null
    echo "=== $m run $i ($branch)" >&2
    t0=$(date +%s)
    MODELS_FILE="$MF" DISPATCH_ALLOW_NO_TIER=1 \
      bash "$REPO/dispatch.sh" --backend codex --worktree "$branch" \
        "$m" "$REPO" "$prompt" "" "bash $HERE/verify.sh" 3 2>"$OUT/$tag.stderr"
    rc=$?; wall=$(( $(date +%s) - t0 ))
    wt="$(sed -n 's/^\[dispatch\] worktree: //p' "$OUT/$tag.stderr" | tail -1)"
    res='{"passed":0,"total":0,"failures":["no worktree"]}'
    [ -n "$wt" ] && res="$(python3 "$HERE/check.py" "$wt")"
    python3 -c 'import json,sys; r=json.loads(sys.argv[4]); r.update(model=sys.argv[1],run=int(sys.argv[2]),exit=int(sys.argv[3]),wall_s=int(sys.argv[5]),pair=sys.argv[6]); print(json.dumps(r))' \
      "$m" "$i" "$rc" "$res" "$wall" "$PAIR" | tee -a "$OUT/results.jsonl"
    rm -f "$prompt"
  done
done
