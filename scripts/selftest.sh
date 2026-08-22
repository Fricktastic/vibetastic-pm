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
  CODEX_FIRST_EVENT_TIMEOUT=0 OPENCODE_DISPATCH_LOG_DIR="$CODEX_LOGS" \
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
  OPENCODE_DISPATCH_LOG_DIR="$CLAUDE_LOGS" \
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

echo "[selftest] plan-lint handles a real live PLAN without crashing"
# Absolute paths on purpose: relative ones do not resolve inside a git worktree, where the
# [ -r ] guard silently turned a skipped check into a pass and gave false confidence.
for live in /Users/tim/Developer/gamedaytastic-pm/PLAN.md /Users/tim/Developer/hometastic-pm/PLAN.md; do
  if [ ! -r "$live" ]; then printf '  skip %s (not present)\n' "$live"; continue; fi
  bash scripts/plan-lint.sh "$live" >/dev/null 2>&1; got=$?
  if [ "$got" -le 3 ]; then pass "$live (exit $got)"; else fail "$live (crashed, exit $got)"; fi
done

echo "[selftest] Xcode verification-boundary preamble (issue #37)"
# Sourcing dispatch.sh is not possible (it runs), so exercise prompt_preamble in isolation by
# extracting the function and driving it with the four cases that matter. Bound the extraction
# so a rename fails loudly instead of silently testing nothing (issue #34).
PRE_SNIP="$(awk '/^prompt_preamble\(\) \{$/,/^\}$/' dispatch.sh)"
if [ -z "$PRE_SNIP" ]; then
  fail "prompt_preamble() not found in dispatch.sh — preamble checks did not run"
else
  PRE_TMP="$(mktemp -d)"
  printf '## T099\nDo the thing.\n' > "$PRE_TMP/task.md"
  pre_case() {  # backend, read_only, verify_cmd, expect(none|generic|codex), label
    local out
    out="$(BACKEND="$1" READ_ONLY="$2" VERIFY_CMD="$3" PROMPT_FILE="$PRE_TMP/task.md" \
      bash -c "READ_ONLY() { [ \"\$READ_ONLY\" = true ]; }
$(printf '%s' "$PRE_SNIP" | sed 's/^  \$READ_ONLY && return 0$/  [ "$READ_ONLY" = true ] \&\& return 0/')
prompt_preamble" 2>/dev/null)"
    case "$4" in
      none)    [ -z "$out" ] && pass "$5" || fail "$5 (expected no preamble, got ${#out} chars)" ;;
      generic) if [ -n "$out" ] && ! printf '%s' "$out" | grep -q 'workspace-write'; then
                 pass "$5"; else fail "$5 (expected generic-only preamble)"; fi ;;
      codex)   if printf '%s' "$out" | grep -q 'workspace-write' \
                 && printf '%s' "$out" | grep -q 'never report a test result'; then
                 pass "$5"; else fail "$5 (expected codex sandbox paragraph + evidence rule)"; fi ;;
    esac
  }
  pre_case codex    false "xcodebuild build-for-testing" codex   "codex + Xcode gets the sandbox paragraph"
  pre_case opencode false "xcodebuild build-for-testing" generic "non-codex Xcode gets the evidence rule only"
  pre_case codex    true  "xcodebuild build-for-testing" none    "read-only dispatch gets no preamble"
  pre_case codex    false "swift build"                  none    "non-Xcode task gets no preamble"
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

echo
if [ "$FAIL" = 0 ]; then echo "[selftest] PASS"; else echo "[selftest] FAIL"; fi
exit "$FAIL"
