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
plan-risk-vocab.md:3
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

echo "[selftest] plan-lint PostToolUse hook maps exit codes to hook behaviour"
# The hook is the mechanical half of the "run plan-lint after every PLAN.md write" rule
# (.claude/rules/state.md).  Its whole value is the exit-code mapping: STRUCTURAL must block
# (hook exit 2, feeding the linter output back to the model), vocabulary drift must NOT
# ([0b] — a permanently red linter gets ignored), and nothing may ever block unrelated work.
# It resolves plan-lint.sh as a SIBLING; an earlier revision walked up from the hook to guess
# the pm dir, which overshot in this repo and silently linted nothing while looking installed.
HOOK_TMP="$(mktemp -d)"
hook_case() {  # fixture, expected hook exit, label
  cp "tests/fixtures/$1" "$HOOK_TMP/PLAN.md"
  printf '{"tool_input":{"file_path":"%s/PLAN.md"}}' "$HOOK_TMP" \
    | python3 scripts/plan-lint-hook.py >/dev/null 2>&1
  got=$?
  if [ "$got" = "$2" ]; then pass "$3"; else fail "$3 (expected hook exit $2, got $got)"; fi
}
hook_case plan-missing-field.md   2 "structural corruption blocks the write (hook exit 2)"
hook_case plan-nested-depends.md  2 "nested depends_on blocks the write"
hook_case plan-vocab.md           0 "vocabulary drift does not block"
hook_case plan-good.md            0 "a clean PLAN.md is silent"
printf '{"tool_input":{"file_path":"%s/dispatch.sh"}}' "$PWD" \
  | python3 scripts/plan-lint-hook.py >/dev/null 2>&1
if [ $? = 0 ]; then pass "a non-PLAN.md write is ignored"; else fail "a non-PLAN.md write was not ignored"; fi
printf 'not json' | python3 scripts/plan-lint-hook.py >/dev/null 2>&1
if [ $? = 0 ]; then pass "malformed hook input never blocks"; else fail "malformed hook input blocked the call"; fi
rm -rf "$HOOK_TMP"

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

echo "[selftest] dispatch logs anchor to the PM dir, not the prompt's dir (issue #17)"
# A prompt in /tmp wrote /tmp/logs/; specs under logs/specs/ produced logs/logs/. Drive an
# early exit (no verify-cmd) from copies of dispatch.sh in each supported layout and check
# where runs.jsonl lands. The prompt deliberately lives outside every PM dir.
LD_TMP="$(cd "$(mktemp -d)" && pwd -P)"
mkdir -p "$LD_TMP/pm/framework" "$LD_TMP/src" "$LD_TMP/explicit" "$LD_TMP/elsewhere/specs"
cp dispatch.sh "$LD_TMP/pm/framework/"; cp dispatch.sh "$LD_TMP/src/"
printf 'selftest prompt\n' > "$LD_TMP/elsewhere/specs/task-T996.md"
ld_case() {  # dispatch.sh copy, PM_DIR ('' = unset), expected log dir, label
  env -u OPENCODE_DISPATCH_LOG_DIR -u PM_DIR ${2:+PM_DIR="$2"} \
    bash "$1" test-model "$LD_TMP" "$LD_TMP/elsewhere/specs/task-T996.md" >/dev/null 2>&1
  if [ -s "$3/runs.jsonl" ] && [ ! -e "$LD_TMP/elsewhere/logs" ] && [ ! -e "$LD_TMP/elsewhere/specs/logs" ]; then
    pass "$4"
  else
    fail "$4 (expected $3/runs.jsonl; logs followed the prompt instead)"
  fi
  rm -rf "$3" "$LD_TMP/elsewhere/logs" "$LD_TMP/elsewhere/specs/logs"
}
ld_case "$LD_TMP/pm/framework/dispatch.sh" "" "$LD_TMP/pm/logs" "installed subtree logs to <pm>/logs"
ld_case "$LD_TMP/src/dispatch.sh" "" "$LD_TMP/src/logs" "a framework source checkout logs beside dispatch.sh"
ld_case "$LD_TMP/pm/framework/dispatch.sh" "$LD_TMP/explicit" "$LD_TMP/explicit/logs" "PM_DIR, when set, decides the log dir"
rm -rf "$LD_TMP"

echo "[selftest] dispatch captures human reports across fake backend turns"
# These fake CLIs make the capture/session assertions hermetic: no credentials, network, or
# real model invocation. They deliberately emit the same JSON payload shapes dispatch parses.
DISPATCH_TMP="$(mktemp -d)"
DISPATCH_BIN="$DISPATCH_TMP/bin"
DISPATCH_PROJECT="$DISPATCH_TMP/project"
mkdir -p "$DISPATCH_BIN" "$DISPATCH_PROJECT"
git -C "$DISPATCH_PROJECT" init -q
printf 'fake task\n' > "$DISPATCH_TMP/task-T998.md"

cat > "$DISPATCH_BIN/codex" <<'SH'
#!/bin/bash
if [[ " $* " == *" resume "* ]]; then
  printf '%s\n' "$*" >> "$FAKE_CALLS"
  touch "$FAKE_VERIFY_FLAG"
  printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"resume report"}}'
  printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":3}}'
else
  printf '%s\n' '{"type":"thread.started","thread_id":"thread-selftest"}'
  printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"fresh first report"}}'
  printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"fresh second report"}}'
  printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
fi
SH
cat > "$DISPATCH_BIN/claude" <<'SH'
#!/bin/bash
if [[ " $* " == *" --resume "* ]]; then
  printf '%s\n' "$*" >> "$FAKE_CALLS"
  touch "$FAKE_VERIFY_FLAG"
  printf '%s\n' '{"session_id":"claude-selftest","result":"claude resume report","usage":{"input_tokens":2,"output_tokens":3}}'
else
  printf '%s\n' '{"session_id":"claude-selftest","result":"claude fresh report","usage":{"input_tokens":1,"output_tokens":1}}'
fi
SH
cat > "$DISPATCH_BIN/opencode" <<'SH'
#!/bin/bash
printf 'call\n' >> "$FAKE_STALL_CALLS"
printf 'fake opencode diagnostic\n' >&2
exit 0
SH
chmod +x "$DISPATCH_BIN/codex" "$DISPATCH_BIN/claude" "$DISPATCH_BIN/opencode"

CODEX_LOGS="$DISPATCH_TMP/codex-logs"
CODEX_CALLS="$DISPATCH_TMP/codex-calls"
CODEX_FLAG="$DISPATCH_TMP/codex-verified"
PATH="$DISPATCH_BIN:$PATH" FAKE_CALLS="$CODEX_CALLS" FAKE_VERIFY_FLAG="$CODEX_FLAG" \
  CODEX_FIRST_EVENT_TIMEOUT=0 OPENCODE_DISPATCH_LOG_DIR="$CODEX_LOGS" DISPATCH_ALLOW_NO_WORKTREE=1 \
  bash dispatch.sh --backend codex gpt-5.6-terra "$DISPATCH_PROJECT" "$DISPATCH_TMP/task-T998.md" \
  '' "test -f $CODEX_FLAG" 2 standard > "$DISPATCH_TMP/codex.stdout" 2> "$DISPATCH_TMP/codex.stderr"
got=$?
CODEX_LOG="$(find "$CODEX_LOGS" -name '*.log' -type f | head -n 1)"
if [ "$got" = 0 ] \
  && grep -q "codex events: ${CODEX_LOG%.log}.codex-events.jsonl" "$DISPATCH_TMP/codex.stderr" \
  && python3 - "$CODEX_LOG" "$DISPATCH_TMP/codex.stdout" "$CODEX_CALLS" \
  "${CODEX_LOG%.log}.codex-events.jsonl" <<'PY'
import sys
log, out, calls, events = map(open, sys.argv[1:])
log, out, calls, events = (f.read() for f in (log, out, calls, events))
assert log.count('turn 1 stdout') == 1 and log.count('turn 2 stdout') == 1
assert log.index('fresh first report') < log.index('fresh second report') < log.index('resume report')
assert out.index('fresh first report') < out.index('fresh second report') < out.index('resume report')
assert 'resume thread-selftest' in calls
assert '"type":' not in log
assert events.count('"thread_id":"thread-selftest"') == 1
PY
then
  pass "codex multi-turn human capture, multi-message order, and session resume"
else
  fail "codex multi-turn capture/session propagation (dispatch exit $got)"
fi

CODEX_READONLY_LOGS="$DISPATCH_TMP/codex-readonly-logs"
PATH="$DISPATCH_BIN:$PATH" CODEX_FIRST_EVENT_TIMEOUT=0 OPENCODE_DISPATCH_LOG_DIR="$CODEX_READONLY_LOGS" \
  bash dispatch.sh --read-only --backend codex gpt-5.6-terra "$DISPATCH_PROJECT" "$DISPATCH_TMP/task-T998.md" \
  > /dev/null 2> "$DISPATCH_TMP/codex-readonly.stderr"
got=$?
CODEX_READONLY_LOG="$(find "$CODEX_READONLY_LOGS" -name '*.log' -type f | head -n 1)"
if [ "$got" = 0 ] && grep -q 'fresh first report' "$CODEX_READONLY_LOG"; then
  pass "codex read-only log contains the assistant report"
else
  fail "codex read-only stdout capture (dispatch exit $got)"
fi

CLAUDE_LOGS="$DISPATCH_TMP/claude-logs"
CLAUDE_CALLS="$DISPATCH_TMP/claude-calls"
CLAUDE_FLAG="$DISPATCH_TMP/claude-verified"
PATH="$DISPATCH_BIN:$PATH" FAKE_CALLS="$CLAUDE_CALLS" FAKE_VERIFY_FLAG="$CLAUDE_FLAG" \
  OPENCODE_DISPATCH_LOG_DIR="$CLAUDE_LOGS" DISPATCH_ALLOW_NO_WORKTREE=1 \
  bash dispatch.sh --backend claude claude-sonnet-4.6 "$DISPATCH_PROJECT" "$DISPATCH_TMP/task-T998.md" \
  '' "test -f $CLAUDE_FLAG" 2 standard > /dev/null 2> "$DISPATCH_TMP/claude.stderr"
got=$?
CLAUDE_LOG="$(find "$CLAUDE_LOGS" -name '*.log' -type f | head -n 1)"
if [ "$got" = 0 ] \
  && grep -q "claude results: ${CLAUDE_LOG%.log}.claude-results.jsonl" "$DISPATCH_TMP/claude.stderr" \
  && python3 - "$CLAUDE_LOG" "$CLAUDE_CALLS" "${CLAUDE_LOG%.log}.claude-results.jsonl" <<'PY'
import sys
log, calls, results = (open(p).read() for p in sys.argv[1:])
assert log.index('claude fresh report') < log.index('claude resume report')
assert 'resume claude-selftest' in calls
assert '"session_id"' not in log
assert results.count('"session_id":"claude-selftest"') == 2
PY
then
  pass "claude capture keeps JSONL sibling-only and preserves session resume"
else
  fail "claude capture/session propagation (dispatch exit $got)"
fi

STALL_LOGS="$DISPATCH_TMP/stall-logs"
STALL_CALLS="$DISPATCH_TMP/stall-calls"
PATH="$DISPATCH_BIN:$PATH" FAKE_STALL_CALLS="$STALL_CALLS" \
  OPENCODE_DISPATCH_LOG_DIR="$STALL_LOGS" OPENCODE_DISPATCH_STALL_RETRIES=1 \
  bash dispatch.sh --read-only --backend opencode openrouter/deepseek/deepseek-v4-pro \
  "$DISPATCH_PROJECT" "$DISPATCH_TMP/task-T998.md" > /dev/null 2> "$DISPATCH_TMP/stall.stderr"
got=$?
STALL_LOG="$(find "$STALL_LOGS" -name '*.log' -type f | head -n 1)"
if [ "$got" = 1 ] && [ "$(wc -l < "$STALL_CALLS" | tr -d ' ')" = 2 ] \
  && grep -q 'empty-output exit 0 reclassified as failure' "$STALL_LOG" \
  && [ "$(grep -c 'fake opencode diagnostic' "$STALL_LOG")" = 2 ]; then
  pass "empty fresh output retries, reclassifies, and retains stderr diagnostics"
else
  fail "empty-fresh stall guard (dispatch exit $got)"
fi
rm -rf "$DISPATCH_TMP"

echo "[selftest] frontmatter prompts are never parsed as CLI options (issue #49)"
# Rendered role prompts start with `---`. Each fake CLI mimics the real parsers' failure: a
# positional beginning with `-` that is not behind an end-of-options `--` is rejected, exactly
# as codex ("unexpected argument"), claude ("unknown option") and opencode (usage) did.
FM_TMP="$(mktemp -d)"; FM_BIN="$FM_TMP/bin"; FM_PROJ="$FM_TMP/project"
mkdir -p "$FM_BIN" "$FM_PROJ"; git -C "$FM_PROJ" init -q
printf -- '---\nrole: tech-lead\n---\n# Task\n' > "$FM_TMP/role-frontmatter.md"
fm_fake() {  # cli, success output
  cat > "$FM_BIN/$1" <<SH
#!/bin/bash
ended=0
for a in "\$@"; do
  if [ "\$ended" = 0 ] && [ "\$a" = -- ]; then ended=1; continue; fi
  case "\$a" in ---*) [ "\$ended" = 1 ] || { echo "error: unexpected argument '\$a' found" >&2; exit 2; } ;; esac
done
printf '%s\n' '$2'
SH
  chmod +x "$FM_BIN/$1"
}
fm_fake codex    '{"type":"item.completed","item":{"type":"agent_message","text":"fm ok"}}'
fm_fake claude   '{"session_id":"fm","result":"fm ok"}'
fm_fake opencode 'fm ok'
for fm_case in "codex gpt-5.6-terra" "claude sonnet" "opencode openrouter/minimax/minimax-m3"; do
  fm_be="${fm_case%% *}"; fm_model="${fm_case#* }"
  PATH="$FM_BIN:$PATH" CODEX_FIRST_EVENT_TIMEOUT=0 OPENCODE_DISPATCH_LOG_DIR="$FM_TMP/logs-$fm_be" \
    bash dispatch.sh --read-only --backend "$fm_be" "$fm_model" "$FM_PROJ" "$FM_TMP/role-frontmatter.md" \
    > "$FM_TMP/out" 2>/dev/null
  got=$?
  if [ "$got" = 0 ] && grep -q 'fm ok' "$FM_TMP/out"; then
    pass "$fm_be receives a '---' prompt as a positional, not an option"
  else
    fail "$fm_be parsed a frontmatter prompt as a CLI option (dispatch exit $got)"
  fi
done
rm -rf "$FM_TMP"

echo "[selftest] --worktree paths are keyed on the branch, not the prompt (issue #45)"
# Field case: three parallel dispatches of ONE prompt on three branches shared one worktree
# and one branch; two branches were never created. Sequential runs reproduce it — the second
# dispatch silently reused the first one's path.
WT_TMP="$(mktemp -d)"; WT_BIN="$WT_TMP/bin"; WT_PROJ="$WT_TMP/project"
mkdir -p "$WT_BIN" "$WT_PROJ"; git -C "$WT_PROJ" init -q
git -C "$WT_PROJ" -c user.name=selftest -c user.email=selftest@example.invalid commit -q --allow-empty -m init
printf 'task\n' > "$WT_TMP/task-T990.md"; printf 'fixup\n' > "$WT_TMP/fixup-T990.md"
cat > "$WT_BIN/claude" <<'SH'
#!/bin/bash
printf '%s %s\n' "$(pwd -P)" "$(git symbolic-ref --short HEAD)" >> "$WT_SEEN"
printf '%s\n' '{"session_id":"wt","result":"wt report"}'
SH
chmod +x "$WT_BIN/claude"
wt_dispatch() {  # branch, prompt -> dispatch exit code
  PATH="$WT_BIN:$PATH" WT_SEEN="$WT_TMP/seen" OPENCODE_DISPATCH_LOG_DIR="$WT_TMP/logs" \
    bash dispatch.sh --worktree "$1" --backend claude sonnet "$WT_PROJ" "$WT_TMP/$2" '' true 1 standard \
    > /dev/null 2> "$WT_TMP/stderr"
}
WT_ROOT_REAL="$(cd "$WT_TMP" && pwd -P)/project-worktrees"
wt_dispatch modeltest/a task-T990.md; got_a=$?
wt_dispatch modeltest/b task-T990.md; got_b=$?
if [ "$got_a" = 0 ] && [ "$got_b" = 0 ] \
  && grep -qx "$WT_ROOT_REAL/modeltest-a modeltest/a" "$WT_TMP/seen" \
  && grep -qx "$WT_ROOT_REAL/modeltest-b modeltest/b" "$WT_TMP/seen"; then
  pass "one prompt on two branches builds in two worktrees, each on its own branch"
else
  fail "same-prompt dispatches on different branches collided (exits $got_a/$got_b): $(tr '\n' ';' < "$WT_TMP/seen")"
fi
: > "$WT_TMP/seen"
wt_dispatch modeltest/a fixup-T990.md; got=$?
if [ "$got" = 0 ] && grep -qx "$WT_ROOT_REAL/modeltest-a modeltest/a" "$WT_TMP/seen"; then
  pass "a fixup prompt on an existing branch reuses that branch's worktree (issue #2)"
else
  fail "re-dispatch on an existing branch did not reuse its worktree (exit $got)"
fi
: > "$WT_TMP/seen"
wt_dispatch modeltest-a task-T990.md; got=$?
if [ "$got" = 2 ] && [ ! -s "$WT_TMP/seen" ] && grep -q 'Refusing to reuse' "$WT_TMP/stderr"; then
  pass "a path holding a different branch is refused, never reused"
else
  fail "a worktree path holding another branch was reused (exit $got)"
fi
rm -rf "$WT_TMP"

echo "[selftest] a build dispatch without --worktree is refused everywhere (issue #15)"
# Only the leased path used to enforce this; a legacy project could build in the live checkout.
NW_TMP="$(mktemp -d)"; NW_BIN="$NW_TMP/bin"; NW_PROJ="$NW_TMP/project"
mkdir -p "$NW_BIN" "$NW_PROJ"; git -C "$NW_PROJ" init -q
printf 'task\n' > "$NW_TMP/task-T991.md"
printf '#!/bin/bash\nprintf call >> "$NW_CALLS"\nprintf "%%s\\n" %s\n' \
  "'{\"session_id\":\"nw\",\"result\":\"nw report\"}'" > "$NW_BIN/claude"
chmod +x "$NW_BIN/claude"
nw_dispatch() {  # extra env assignment (or ''), extra flag (or '') -> exit code
  env -u DISPATCH_ALLOW_NO_WORKTREE PATH="$NW_BIN:$PATH" NW_CALLS="$NW_TMP/calls" \
    OPENCODE_DISPATCH_LOG_DIR="$NW_TMP/logs" ${1:+"$1"} \
    bash dispatch.sh ${2:+"$2"} --backend claude sonnet "$NW_PROJ" "$NW_TMP/task-T991.md" '' true 1 standard \
    > /dev/null 2> "$NW_TMP/stderr"
}
nw_dispatch '' ''; got=$?
if [ "$got" = 2 ] && [ ! -e "$NW_TMP/calls" ] && grep -q 'no --worktree' "$NW_TMP/stderr"; then
  pass "an unmanaged build dispatch without --worktree is refused before the builder runs"
else
  fail "a build dispatch without --worktree was not refused (exit $got)"
fi
nw_dispatch DISPATCH_ALLOW_NO_WORKTREE=1 ''; got=$?
[ "$got" = 0 ] && pass "DISPATCH_ALLOW_NO_WORKTREE=1 permits a deliberate exception" \
  || fail "DISPATCH_ALLOW_NO_WORKTREE=1 did not permit the dispatch (exit $got)"
rm -f "$NW_TMP/calls"
nw_dispatch '' --read-only; got=$?
[ "$got" = 0 ] && pass "a --read-only dispatch needs no worktree" \
  || fail "a --read-only dispatch was refused for lacking --worktree (exit $got)"
rm -rf "$NW_TMP"

echo "[selftest] partner-burn hook records orchestrator usage"
# The whole cost of issue #30 was that this hook's failure and success looked identical from
# outside: it read usage off the Stop payload (which carries none), silently wrote nothing for
# five days, and no check existed to notice.  These assertions fail if it ever stops writing.
BURN_TMP="$(mktemp -d)"
cp tests/fixtures/transcript-partner.jsonl "$BURN_TMP/t.jsonl"
burn_hook() {  # session_id, transcript path
  printf '{"session_id":"%s","transcript_path":"%s","stop_hook_active":false,"cwd":"%s"}' \
    "$1" "$2" "$BURN_TMP" \
    | OPENCODE_DISPATCH_LOG_DIR="$BURN_TMP/logs" python3 scripts/log-partner-burn.py >/dev/null 2>&1
}
burn_hook s1 "$BURN_TMP/t.jsonl"
if [ -s "$BURN_TMP/logs/cost.jsonl" ] && python3 - "$BURN_TMP/logs/cost.jsonl" <<'PYCHK'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
assert len(rows) == 1, rows
r = rows[0]
assert r["role"] == "partner" and r["backend"] == "claude"
# Sidechain (subagent) turns belong to agent-spawns.jsonl and must not be counted here:
# the fixture's sidechain turn carries 999s in every field.
assert r["input_tokens"] == 15 and r["output_tokens"] == 150, r
assert r["cache_read_tokens"] == 3000 and r["cache_creation_tokens"] == 75, r
assert r["reasoning_tokens"] == 10, r
PYCHK
then pass "records a session's usage, excluding sidechain turns"
else fail "partner-burn wrote no usable record"; fi

burn_hook s1 "$BURN_TMP/t.jsonl"
if [ "$(wc -l < "$BURN_TMP/logs/cost.jsonl")" -eq 1 ]; then
  pass "an unchanged transcript adds nothing (no cumulative double-count)"
else
  fail "partner-burn re-counted an unchanged transcript"
fi

cat >> "$BURN_TMP/t.jsonl" <<'JSONTURN'
{"type":"assistant","message":{"role":"assistant","model":"claude-opus-5","usage":{"input_tokens":1,"output_tokens":9,"cache_read_input_tokens":300,"cache_creation_input_tokens":0}}}
JSONTURN
burn_hook s1 "$BURN_TMP/t.jsonl"
if python3 - "$BURN_TMP/logs/cost.jsonl" <<'PYDELTA'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
assert len(rows) == 2, rows
assert (rows[1]["input_tokens"], rows[1]["output_tokens"]) == (1, 9), rows[1]
PYDELTA
then pass "a grown transcript logs only the delta"
else fail "partner-burn delta accounting"; fi

burn_hook s2 "tests/fixtures/transcript-no-usage.jsonl"
if [ "$(wc -l < "$BURN_TMP/logs/cost.jsonl")" -eq 2 ] \
  && grep -q 'carried no usage' "$BURN_TMP/logs/telemetry-errors.log"; then
  pass "a transcript with no usage writes no zeros, but leaves a diagnostic"
else
  fail "partner-burn silent-failure guard"
fi

burn_hook s3 "$BURN_TMP/does-not-exist.jsonl"
if grep -q 'no readable transcript_path' "$BURN_TMP/logs/telemetry-errors.log"; then
  pass "a missing transcript is recorded as a telemetry error"
else
  fail "partner-burn did not report a missing transcript"
fi
printf 'not json' | OPENCODE_DISPATCH_LOG_DIR="$BURN_TMP/logs" python3 scripts/log-partner-burn.py >/dev/null 2>&1
if [ $? = 0 ]; then pass "malformed hook input never fails the session"; else fail "partner-burn blocked on malformed input"; fi
rm -rf "$BURN_TMP"

echo "[selftest] live PLANs pass the plan-lint exit contract (0/3 ok; 1/2/crash fail — issue #24)"
# Absolute paths on purpose: relative ones do not resolve inside a git worktree, where the
# [ -r ] guard silently turned a skipped check into a pass and gave false confidence.
#
# Which deployments to lint is DISCOVERED or configured locally — never hardcoded. This repo
# is public; the paths to someone's working directories are not framework content. Precedence:
#
#   1. $SELFTEST_LIVE_PLANS       — space-separated absolute paths (one-off / CI)
#   2. .selftest-live-plans       — gitignored local file, one absolute path per line,
#                                   `#` comments allowed. This is where to record the projects
#                                   you actually develop against.
#   3. sibling `*-pm/PLAN.md`     — anything next to this checkout
#
# The repo root comes from --git-common-dir so it resolves to the MAIN checkout even when the
# selftest runs inside a worktree.
_repo_root="$(cd "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo .)")" 2>/dev/null && pwd)"
[ -n "$_repo_root" ] || _repo_root="$(pwd)"
_live_cfg="$_repo_root/.selftest-live-plans"
set --
if [ -n "${SELFTEST_LIVE_PLANS:-}" ]; then
  # shellcheck disable=SC2086
  set -- $SELFTEST_LIVE_PLANS
elif [ -r "$_live_cfg" ]; then
  while IFS= read -r _line; do
    _line="${_line%%#*}"
    _line="$(printf '%s' "$_line" | tr -d '[:space:]')"
    [ -n "$_line" ] && set -- "$@" "$_line"
  done < "$_live_cfg"
  [ $# -gt 0 ] || echo "  skip (.selftest-live-plans is present but lists no paths)"
else
  for _cand in "$(dirname "$_repo_root")"/*-pm/PLAN.md; do
    [ -r "$_cand" ] && [ "$_cand" != "$_repo_root/PLAN.md" ] && set -- "$@" "$_cand"
  done
  [ $# -gt 0 ] || echo "  skip (no sibling *-pm/PLAN.md found; see .selftest-live-plans.example)"
fi
# [issue #24] The lint exit contract (.claude/rules/state.md): 0 clean and 3 vocabulary drift
# pass; 1 is STRUCTURAL corruption, 2 unreadable, anything else a crash — all fail. This used
# to pass every exit <= 3, so a structurally corrupt live PLAN printed "ok (exit 1)".
live_lint() {  # path -> pass/fail line per the exit contract
  bash scripts/plan-lint.sh "$1" >/dev/null 2>&1; local got=$?
  case "$got" in
    0|3) pass "$1 (exit $got)" ;;
    1)   fail "$1 (STRUCTURALLY CORRUPT, plan-lint exit 1)" ;;
    2)   fail "$1 (unreadable, plan-lint exit 2)" ;;
    *)   fail "$1 (crashed, exit $got)" ;;
  esac
}
# The classifier itself, against fixtures, in a subshell so its verdicts do not touch FAIL.
for lc in plan-good.md:ok plan-vocab.md:ok plan-bad-escape.md:FAIL plan-missing-field.md:FAIL; do
  lc_verdict="$( (live_lint "tests/fixtures/${lc%%:*}") | awk '{print $1}')"
  if [ "$lc_verdict" = "${lc##*:}" ]; then pass "live-PLAN check: ${lc%%:*} -> ${lc##*:}"
  else fail "live-PLAN check misclassified ${lc%%:*} (got '$lc_verdict', want '${lc##*:}')"; fi
done
for live in "$@"; do
  if [ ! -r "$live" ]; then printf '  skip %s (not present)\n' "$live"; continue; fi
  live_lint "$live"
done

echo "[selftest] state_correction is a documented event type carrying evidence (issue #29)"
# TASK_LOG events have no mechanical validator; the vocabulary lives in the shipped TASK_LOG
# template and state.md. Assert both define it, and that the state.md entry template requires
# `evidence:` — a correction must carry its own proof.
if python3 - TASK_LOG.md .claude/rules/state.md <<'PY'
import re, sys
log, state = (open(p).read() for p in sys.argv[1:3])
vocab = log.split('Valid event_type values:', 1)[1].split('-->', 1)[0]
assert re.search(r'^\s+state_correction\s+-', vocab, re.M), 'missing from TASK_LOG template'
block = re.search(r'### <ISO8601> · state_correction\n```yaml\n(.*?)\n```', state, re.S)
assert block, 'no state_correction entry template in state.md'
assert re.search(r'^evidence:.*REQUIRED', block.group(1), re.M), 'evidence: not required'
PY
then pass "TASK_LOG template and state.md define state_correction with required evidence"
else fail "state_correction is undocumented or does not require evidence"; fi

echo "[selftest] builder preamble: working agreement (issue #50) + Xcode boundary (issue #37)"
# Sourcing dispatch.sh is not possible (it runs), so exercise prompt_preamble in isolation by
# extracting the function and driving it with the cases that matter. Bound the extraction
# so a rename fails loudly instead of silently testing nothing (issue #34).
PRE_SNIP="$(awk '/^prompt_preamble\(\) \{$/,/^\}$/' dispatch.sh)"
if [ -z "$PRE_SNIP" ]; then
  fail "prompt_preamble() not found in dispatch.sh — preamble checks did not run"
else
  PRE_TMP="$(mktemp -d)"
  printf '## T099\nDo the thing.\n' > "$PRE_TMP/task.md"
  pre_case() {  # backend, read_only, verify_cmd, expect(none|autonomy|generic|codex), label
    local out
    out="$(BACKEND="$1" READ_ONLY="$2" VERIFY_CMD="$3" PROMPT_FILE="$PRE_TMP/task.md" \
      bash -c "READ_ONLY() { [ \"\$READ_ONLY\" = true ]; }
$(printf '%s' "$PRE_SNIP" | sed 's/^  \$READ_ONLY && return 0$/  [ "$READ_ONLY" = true ] \&\& return 0/')
prompt_preamble" 2>/dev/null)"
    has() { printf '%s' "$out" | grep -q "$1"; }
    case "$4" in
      none)     [ -z "$out" ] && pass "$5" || fail "$5 (expected no preamble, got ${#out} chars)" ;;
      autonomy) if has 'Do not stop' && ! has 'Verification boundary'; then
                  pass "$5"; else fail "$5 (expected the working agreement only)"; fi ;;
      generic)  if has 'Do not stop' && has 'never report a test result' && ! has 'workspace-write'; then
                  pass "$5"; else fail "$5 (expected working agreement + generic evidence rule)"; fi ;;
      codex)    if has 'Do not stop' && has 'workspace-write' && has 'never report a test result'; then
                  pass "$5"; else fail "$5 (expected working agreement + codex sandbox paragraph + evidence rule)"; fi ;;
    esac
  }
  pre_case codex    false "xcodebuild build-for-testing" codex    "codex + Xcode gets the sandbox paragraph"
  pre_case opencode false "xcodebuild build-for-testing" generic  "non-codex Xcode gets the evidence rule only"
  pre_case codex    true  "xcodebuild build-for-testing" none     "read-only dispatch gets no preamble"
  pre_case codex    false "swift build"                  autonomy "non-Xcode build gets the do-not-ask working agreement"
  pre_case claude   false "npm test"                     autonomy "every backend's build gets the working agreement"
  rm -rf "$PRE_TMP"
fi

echo "[selftest] verify-cmd that cannot compile tests warns (issue #36)"
vwarn_case() {  # verify_cmd, expect(warn|quiet), label
  local out
  out="$(VERIFY_CMD="$1" bash -c "$(awk '/^# --- \[issue #36\/#37\] A verify-cmd/,/^esac$/' dispatch.sh)" 2>&1)"
  case "$2" in
    warn)  printf '%s' "$out" | grep -q 'build-for-testing' && pass "$3" || fail "$3 (expected a warning)" ;;
    quiet) [ -z "$out" ] && pass "$3" || fail "$3 (expected silence, got: $out)" ;;
  esac
}
vwarn_case "xcodegen generate && xcodebuild -scheme App build" warn  "bare xcodebuild build warns"
vwarn_case "xcodebuild build-for-testing -scheme App"          quiet "build-for-testing is silent"
vwarn_case "swift build"                                       quiet "non-Xcode verify-cmd is silent"

echo "[selftest] spec-body-guard enforces dispatch.md [0g] (issue #39)"
guard_case() {  # expected exit, hook stdin json, label
  printf '%s' "$2" | python3 scripts/spec-body-guard.py >/dev/null 2>&1
  got=$?
  if [ "$got" = "$1" ]; then pass "$3"; else fail "$3 (expected exit $1, got $got)"; fi
}
# blocked: whole-body reads of a task/critic spec
guard_case 2 '{"tool_name":"Read","tool_input":{"file_path":"/p/prompts/task-T065.md"}}'              "Read of a whole task spec is blocked"
guard_case 2 '{"tool_name":"Read","tool_input":{"file_path":"/p/prompts/critic-T071.md"}}'            "Read of a whole critic spec is blocked"
guard_case 2 '{"tool_name":"Read","tool_input":{"file_path":"/p/prompts/task-T065.md","limit":500}}'  "Read with an unbounded limit is blocked"
guard_case 2 '{"tool_name":"Bash","tool_input":{"command":"cat prompts/task-T065.md"}}'               "cat of a task spec is blocked"
guard_case 2 '{"tool_name":"Bash","tool_input":{"command":"head -n 400 prompts/task-T065.md"}}'       "an oversized head is blocked"
# allowed: everything [0g] steps 1-2 actually need
guard_case 0 '{"tool_name":"Read","tool_input":{"file_path":"/p/prompts/task-T065.md","limit":30}}'   "a bounded Read is allowed"
guard_case 0 '{"tool_name":"Bash","tool_input":{"command":"test -s prompts/task-T065.md"}}'           "the non-empty check is allowed"
guard_case 0 '{"tool_name":"Bash","tool_input":{"command":"sed -n /TECH_LEAD_RESULT_START/,$p prompts/task-T065.md"}}' "YAML extraction is allowed"
guard_case 0 '{"tool_name":"Bash","tool_input":{"command":"grep -n verify_tier prompts/task-T065.md"}}' "grep is allowed"
guard_case 0 '{"tool_name":"Bash","tool_input":{"command":"tail -n 40 prompts/task-T065.md"}}'        "a bounded tail is allowed"
guard_case 0 '{"tool_name":"Bash","tool_input":{"command":"bash framework/dispatch.sh --worktree b m ../x/ prompts/task-T065.md"}}' "dispatching the spec is allowed"
guard_case 0 '{"tool_name":"Read","tool_input":{"file_path":"/p/prompts/build-spec.md"}}'             "build-spec.md is not a task spec"
guard_case 0 '{"tool_name":"Read","tool_input":{"file_path":"/p/PLAN.md"}}'                           "PLAN.md is untouched"
guard_case 0 '{"tool_name":"Edit","tool_input":{"file_path":"/p/prompts/task-T065.md"}}'              "a non-Read/Bash tool is ignored"
guard_case 0 'not json'                                                                               "malformed hook input never blocks"
if SPEC_BODY_GUARD_OFF=1 sh -c 'printf "%s" "{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"/p/prompts/task-T065.md\"}}" | python3 scripts/spec-body-guard.py' >/dev/null 2>&1
then pass "SPEC_BODY_GUARD_OFF=1 permits a deliberate exception"
else fail "SPEC_BODY_GUARD_OFF=1 did not permit the read"; fi

# The hook is worthless unwired: setup.sh must actually install it as a PreToolUse hook.
if grep -q 'spec-body-guard.py' setup.sh && grep -q '"PreToolUse"' setup.sh; then
  pass "setup.sh wires spec-body-guard as a PreToolUse hook"
else
  fail "setup.sh does not wire spec-body-guard — the gate would look installed and enforce nothing"
fi

echo "[selftest] tier ladder + sol@high burn gate (issue #41)"
# The gate block runs before any backend work, so drive it in isolation rather than paying for
# a real dispatch. Extract by MARKER PAIR: bounding it on /^fi$/ truncated at the first block
# and silently left three gates untested — a check that cannot fail (#34).
GATE_BLOCK="$(awk '/^# --- \[issue #41\] The escalation ladder/,/^# --- \[issue #41\] end of ladder/' dispatch.sh)"
if [ -z "$GATE_BLOCK" ] || ! printf '%s' "$GATE_BLOCK" | grep -q 'burn gate'; then
  fail "the issue-#41 gate block markers are missing from dispatch.sh — gate checks did not run"
else
  GATE_TMP="$(mktemp -d)"; mkdir -p "$GATE_TMP/prompts" "$GATE_TMP/logs"
  echo task > "$GATE_TMP/prompts/task-T001.md"
  printf -- '---\ncodex_weekly_burn_threshold: 4000000\n---\n' > "$GATE_TMP/PROJECT.md"
  printf '%s\n' "$GATE_BLOCK" > "$GATE_TMP/gate.sh"
  gate_case() {  # want_exit, backend, model, tier, read_only, label
    local got
    ( BACKEND="$2"; MODEL="$3"; TIER="$4"; READ_ONLY="$5"
      LOG_DIR="$GATE_TMP/logs"; MODELS_FILE="$PWD/MODELS.md"
      PROMPT_FILE="$GATE_TMP/prompts/task-T001.md"
      # The gate block honours two ambient escape hatches (DISPATCH_ALLOW_NO_TIER,
      # DISPATCH_ALLOW_UNLADDERED_HIGH). These cases assert the DEFAULT refusing behaviour, so
      # an exported override must not leak in — with DISPATCH_ALLOW_NO_TIER=1 in the caller's
      # shell the "no tier is refused" case exits 0 instead of 2 and the suite goes green while
      # testing nothing. Observed for real: every dispatch in the 2026-08-22 OpenRouter tier
      # bake-off set that variable, and three of the four builders independently diagnosed it.
      # The #34 pattern in this file's own code.
      unset DISPATCH_ALLOW_NO_TIER DISPATCH_ALLOW_UNLADDERED_HIGH
      model_of()  { echo "${1%%@*}"; }
      effort_of() { case "$1" in *@*) echo "${1##*@}" ;; *) echo "" ;; esac; }
      . "$GATE_TMP/gate.sh" ) >/dev/null 2>&1
    got=$?
    if [ "$got" = "$1" ]; then pass "$6"; else fail "$6 (expected exit $1, got $got)"; fi
  }
  # tier must select the model (11 field runs contradicted MODELS.md)
  gate_case 2 codex    gpt-5.6-sol@high    standard false "tier standard + sol@high is refused"
  gate_case 0 codex    gpt-5.6-terra       standard false "tier standard + terra is allowed"
  gate_case 2 codex    gpt-5.6-terra       fast     false "tier fast + terra is refused"
  gate_case 0 codex    gpt-5.6-luna        fast     false "tier fast + luna is allowed"
  gate_case 0 codex    gpt-5.6-sol@medium  heavy    false "a within-rung effort bump still matches heavy"
  gate_case 0 claude   sonnet              standard false "claude standard + sonnet is allowed"
  gate_case 2 claude   sonnet              heavy    false "claude heavy + sonnet is refused (heavy=opus)"
  gate_case 0 opencode openrouter/minimax/minimax-m3       standard false "opencode standard resolves"
  gate_case 2 opencode openrouter/google/gemini-2.5-flash  fast false "a retired model is not a live rung"
  # missing tier (issue #19 — was a warning, missing on 78% of runs)
  gate_case 2 codex    gpt-5.6-terra       ""       false "a build dispatch with no tier is refused"
  gate_case 0 codex    gpt-5.6-terra       ""       true  "a read-only dispatch needs no tier"
  # sol@high is the terminal rung, and it is burn-gated
  gate_case 2 codex    gpt-5.6-sol@high    heavy    true  "read-only never reaches sol@high"
  gate_case 2 codex    gpt-5.6-sol@high    heavy    false "a first-attempt sol@high is refused"
  GATE_NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts":"%s","backend":"codex","prompt":"task-T001.md","exit":20,"input_tokens":100}\n' \
    "$GATE_NOW" > "$GATE_TMP/logs/cost.jsonl"
  gate_case 0 codex    gpt-5.6-sol@high    heavy    false "sol@high opens after a prior exit 20, under threshold"
  printf '{"ts":"%s","backend":"codex","prompt":"task-T001.md","exit":20,"input_tokens":9000000}\n' \
    "$GATE_NOW" > "$GATE_TMP/logs/cost.jsonl"
  gate_case 30 codex   gpt-5.6-sol@high    heavy    false "sol@high is skipped (exit 30) above the burn threshold"
  rm -rf "$GATE_TMP"
fi

# burn_proxy must reach the telemetry row, or the audit stays discipline-based.
if grep -q '"burn_proxy":%s' dispatch.sh && grep -q '${BURN_PROXY:-null}' dispatch.sh; then
  pass "dispatch.sh stamps burn_proxy into cost.jsonl"
else
  fail "dispatch.sh does not stamp burn_proxy — the sol@high audit would stay manual"
fi

echo "[selftest] handoff volatile section — SessionStart banner (issue #51)"
# The heading is a contract: a consuming project's interim shim greps framework/ for this
# exact string to know it can retire itself. Renaming it silently re-arms the shim.
VOL_HEADING='## Volatile — re-verify before use'
if grep -qF -- "$VOL_HEADING" scripts/handoff-volatile-hook.py && grep -qF -- "$VOL_HEADING" RULES.md; then
  pass "the exact volatile heading is defined in the hook and RULES.md"
else
  fail "the volatile heading drifted — consuming-project shims detect the exact string"
fi
VOL_TMP="$(mktemp -d)"
vol_run() { python3 scripts/handoff-volatile-hook.py --pm-dir "$VOL_TMP" < /dev/null 2>&1; }
vol_case() {  # label, expected-grep ('' = expect silence)
  local out got
  out="$(vol_run)"; got=$?
  if [ "$got" != 0 ]; then fail "$1 (hook exit $got — must never fail a session)"; return; fi
  if [ -z "$2" ]; then
    [ -z "$out" ] && pass "$1" || fail "$1 (expected silence, got: $out)"
  else
    printf '%s' "$out" | grep -qF -- "$2" && pass "$1" || fail "$1 (missing '$2' in: $out)"
  fi
}
vol_case "no HANDOFF.md is a silent no-op" ""
printf '# Handoff\n\n## Next\n- do the thing\n' > "$VOL_TMP/HANDOFF.md"
vol_case "HANDOFF.md without the section is silent" ""
printf '# Handoff\n\n%s\n\n## Next\n- x\n' "$VOL_HEADING" > "$VOL_TMP/HANDOFF.md"
vol_case "an empty volatile section is silent" ""
printf '# Handoff\n\n%s\n- Codex quota exhausted | as-of 2026-09-24 | check: `gh pr view 1 | cat`\n\n## Next\n- next-section-line\n' \
  "$VOL_HEADING" > "$VOL_TMP/HANDOFF.md"
vol_case "a present section prints under the claims-not-facts banner" "CLAIMS, NOT FACTS"
vol_case "each claim is printed with its age" "Codex quota exhausted | as-of 2026-09-24"
if vol_run | grep -q 'next-section-line'; then fail "the section leaked past the next heading"
else pass "the section stops at the next heading"; fi
if vol_run | grep -q 'MALFORMED'; then fail "a well-formed line (pipe inside check) was flagged"
else pass "a check command containing a pipe is still well-formed"; fi
printf '# Handoff\n\n%s\n- device build is at abc123\n- PR open | as-of yesterday | check: `true`\n' \
  "$VOL_HEADING" > "$VOL_TMP/HANDOFF.md"
vol_case "a line with no as-of/check is flagged malformed" "MALFORMED: no as-of date, no check command"
vol_case "a non-ISO as-of date is flagged" "MALFORMED: invalid as-of date"
rm -f "$VOL_TMP/HANDOFF.md"; mkdir "$VOL_TMP/HANDOFF.md"
vol_case "an unreadable HANDOFF.md never fails the session" ""
printf '\xff\xfe%s\n- x | as-of 2026-01-01 | check: `true`\n' "$VOL_HEADING" > "$VOL_TMP/H2"
rm -rf "$VOL_TMP/HANDOFF.md"; mv "$VOL_TMP/H2" "$VOL_TMP/HANDOFF.md"
out="$(vol_run)"; [ $? = 0 ] && pass "undecodable bytes never fail the session" || fail "undecodable HANDOFF.md failed the hook"
rm -rf "$VOL_TMP"
if grep -q 'handoff-volatile-hook.py' scripts/install-orchestrators.py \
   && grep -q 'SessionStart' scripts/install-orchestrators.py \
   && grep -q 'SessionStart' scripts/orchestrator-doctor.py; then
  pass "the installer wires it as SessionStart for both providers and the doctor checks it"
else
  fail "the volatile hook is not wired/checked — it would look shipped and do nothing"
fi

echo "[selftest] dispatch classifies backend quota/auth refusals as exit 30 (issue #51)"
# Before #51 a quota refusal mid-run exited 1 — a task failure toward Gate 2 — so the only
# "quota" signal the orchestrator had was a handoff sentence. These fakes emit each backend's
# refusal shape; the last case guards against classifying the builder's own prose.
Q_TMP="$(mktemp -d)"; Q_BIN="$Q_TMP/bin"; Q_PROJ="$Q_TMP/project"
mkdir -p "$Q_BIN" "$Q_PROJ"; git -C "$Q_PROJ" init -q
printf 'fake task\n' > "$Q_TMP/task-T997.md"
q_case() {  # want_exit, backend, model, label  (fake CLI body already written)
  local got
  PATH="$Q_BIN:$PATH" CODEX_FIRST_EVENT_TIMEOUT=0 OPENCODE_DISPATCH_LOG_DIR="$Q_TMP/logs-$RANDOM" \
    Q_CALLS="$Q_TMP/calls" bash dispatch.sh --read-only --backend "$2" "$3" "$Q_PROJ" \
    "$Q_TMP/task-T997.md" > /dev/null 2> "$Q_TMP/stderr"
  got=$?
  if [ "$got" = "$1" ]; then pass "$4"; else fail "$4 (expected exit $1, got $got)"; fi
}
q_fake() { printf '#!/bin/bash\nprintf "call\\n" >> "$Q_CALLS"\n%s\n' "$2" > "$Q_BIN/$1"; chmod +x "$Q_BIN/$1"; }
q_fake codex "printf '%s\n' '{\"type\":\"error\",\"message\":\"You have hit your usage limit. Try again later.\"}'
printf '%s\n' '{\"type\":\"turn.failed\",\"error\":{\"message\":\"usage limit\"}}'; exit 1"
q_case 30 codex gpt-5.6-terra "codex usage-limit event exits 30 (backend unavailable)"
grep -q 'backend unavailable: codex' "$Q_TMP/stderr" \
  && pass "the refusal reason is reported on stderr" || fail "exit 30 carried no reason"
q_fake claude "printf '%s\n' '{\"is_error\":true,\"result\":\"Claude AI usage limit reached|1790000000\",\"session_id\":\"s\"}'; exit 1"
q_case 30 claude sonnet "claude is_error usage-limit result exits 30"
rm -f "$Q_TMP/calls"
q_fake opencode "echo 'ERROR 2026-09-27 service=llm error=402 Insufficient credits' >&2; exit 1"
q_case 30 opencode openrouter/minimax/minimax-m3 "opencode insufficient-credits error exits 30"
[ "$(wc -l < "$Q_TMP/calls" | tr -d ' ')" = 1 ] \
  && pass "a refused silent run is not stall-retried" || fail "a quota refusal was stall-retried"
q_fake codex "printf '%s\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"added a rate limit and fixed the 429 quota handling\"}}'; exit 1"
q_case 1 codex gpt-5.6-terra "the builder's own report mentioning rate limits stays exit 1"
rm -rf "$Q_TMP"

echo "[selftest] investigate.sh — the one-command diagnosis lane (issue #42)"
INV_TMP="$(mktemp -d)"
mkdir -p "$INV_TMP/framework" "$INV_TMP/target"
cp investigate.sh "$INV_TMP/framework/"
cp -r prompts "$INV_TMP/framework/"
printf 'echo "ARGS: $*"\n' > "$INV_TMP/framework/dispatch.sh"
echo "known so far" > "$INV_TMP/ctx.txt"

inv() { ( cd "$INV_TMP" && bash framework/investigate.sh "$@" 2>&1 ); }
inv_exit() { ( cd "$INV_TMP" && bash framework/investigate.sh "$@" >/dev/null 2>&1 ); echo $?; }

# argument handling
[ "$(inv_exit)" = 2 ]                             && pass "no args prints usage and exits 2"     || fail "no args did not exit 2"
[ "$(inv_exit "$INV_TMP/nope" q)" = 2 ]           && pass "a missing code dir is refused"        || fail "a missing code dir was accepted"
[ "$(inv_exit "$INV_TMP/target" q "" bogus)" = 2 ] && pass "an invalid tier is refused"           || fail "an invalid tier was accepted"
[ "$(inv_exit "$INV_TMP/target" q "$INV_TMP/nope.txt")" = 2 ] && pass "an unreadable context file is refused" || fail "an unreadable context file was accepted"

# it must dispatch READ-ONLY — this lane can never modify the target project
if inv "$INV_TMP/target" "why does it flicker" | grep -q -- '--read-only'; then
  pass "the dispatch is read-only"
else
  fail "investigate.sh dispatched WITHOUT --read-only — a diagnosis lane must never write"
fi

# backend comes from PROJECT.md, tier resolves to a model
printf -- '---\nbuilder_backends: [claude, opencode]\n---\n' > "$INV_TMP/PROJECT.md"
inv "$INV_TMP/target" "q" "" heavy | grep -q -- '--backend claude opus' \
  && pass "PROJECT.md backend + heavy resolves to claude opus" \
  || fail "backend/tier resolution ignored PROJECT.md"
printf -- '---\nbuilder_backends: [codex, claude]\n---\n' > "$INV_TMP/PROJECT.md"
inv "$INV_TMP/target" "q" "" fast | grep -q 'gpt-5.6-luna' \
  && pass "codex + fast resolves to luna" || fail "codex fast did not resolve to luna"

# the context file must actually reach the rendered prompt
inv "$INV_TMP/target" "why does it flicker" "$INV_TMP/ctx.txt" >/dev/null
if grep -rq 'known so far' "$INV_TMP/prompts/" 2>/dev/null; then
  pass "the context file is rendered into the prompt"
else
  fail "the context file never reached the prompt"
fi
if grep -rq 'why does it flicker' "$INV_TMP/prompts/" 2>/dev/null; then
  pass "the question is rendered into the prompt"
else
  fail "the question never reached the prompt"
fi

# investigate.sh carries its own tier->model table; MODELS.md is the source of truth, so
# assert they agree. Without this the two drift silently — the read-only lane passes no
# tier, so dispatch.sh's issue-#41 check never sees this pairing.
for pair in "codex:fast:gpt-5.6-luna" "codex:standard:gpt-5.6-terra" "codex:heavy:gpt-5.6-sol" \
            "opencode:fast:openrouter/deepseek/deepseek-v4-flash-0731" "opencode:standard:openrouter/minimax/minimax-m3" "opencode:heavy:openrouter/moonshotai/kimi-k2.6"; do
  be="${pair%%:*}"; rest="${pair#*:}"; tr_="${rest%%:*}"; want="${rest#*:}"
  got="$(python3 -c "
import re,sys
tier,section=sys.argv[1],sys.argv[2]
lines=open('MODELS.md').read().splitlines()
start=next(i for i,l in enumerate(lines) if l.strip()==section)
depth=section.count('#')
for l in lines[start+1:]:
    if l.startswith('#') and (len(l)-len(l.lstrip('#')))<=depth: break
    m=re.match(r'\|\s*\`'+tier+r'\`\s*\|\s*\`([^\`]+)\`',l)
    if m: print(m.group(1).split('@')[0]); break
" "$tr_" "$([ "$be" = codex ] && echo '### Codex tier column' || echo '## OpenCode Tiers')" 2>/dev/null)"
  if [ "$got" = "$want" ] && grep -q "$want" investigate.sh; then
    pass "investigate.sh $be/$tr_ agrees with MODELS.md"
  else
    fail "investigate.sh $be/$tr_ drifted from MODELS.md (MODELS.md says '$got')"
  fi
done
rm -rf "$INV_TMP"

echo "[selftest] additive orchestration regression suite"
if python3 -m unittest discover -s tests -p 'test_*.py'; then
  pass "lease, transaction, telemetry, hook and routing behavior"
else
  fail "additive orchestration regression suite"
fi

echo
if [ "$FAIL" = 0 ]; then echo "[selftest] PASS"; else echo "[selftest] FAIL"; fi
exit "$FAIL"
