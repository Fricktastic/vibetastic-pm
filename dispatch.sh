#!/bin/bash
# PM dispatch wrapper — called by PM orchestrator to avoid shell substitution in Claude Code permission checks.
#
# Usage:
#   bash dispatch.sh [--read-only] [--worktree <branch>] [--backend <codex|claude|opencode>] \
#     <model> <project-dir> <prompt-file> [fallback-model] [verify-cmd] [max-attempts] [tier]
#
# --backend: which builder CLI runs the task. Default is inferred from the model slug:
#   gpt-* → codex; claude-*/sonnet/opus/haiku → claude; anything else (openrouter/*) → opencode.
#   codex model slugs may carry a reasoning effort suffix: `gpt-5.6-sol@low` (default: the
#   model's own default effort). The claude backend always runs on subscription auth —
#   ANTHROPIC_API_KEY is stripped from its environment (MODELS.md hard invariant).
#
# --read-only: for diagnosis/review dispatches (RULES.md lesson 2, VERIFY.md diff review).
#   The model is expected to investigate and report, changing nothing. Enforced structurally:
#   if the target git tree differs after the run (status, content of tracked files — including
#   files that were already dirty — or content of untracked, non-ignored files), dispatch exits
#   21 (changes left in place for inspection — never auto-reverted). Ignored files are excluded
#   from the snapshot for practicality, so mutating a gitignored file is the one unenforced edge.
#   The verify loop is skipped in this mode.
#
# --worktree <branch>: run the builder in an isolated git worktree instead of the live
#   checkout. The worktree is created at <project-dir>/../<project>-worktrees/<prompt-basename>
#   on <branch> (created from the current HEAD if it doesn't exist; reused if the path
#   already exists from a prior dispatch of the same task, e.g. a tier-escalation re-run).
#   If <branch> is already checked out in ANY existing worktree (e.g. a fixup dispatch with a
#   different prompt name onto the same PR branch), that worktree is reused instead of
#   attempting a colliding `worktree add`.
#   Builder credential stripping: worktree dispatches run opencode with GH_TOKEN/GITHUB_TOKEN
#   unset and GH_CONFIG_DIR pointed at an empty dir, and the worktree's remote.origin.pushurl
#   set (per-worktree config) to an invalid URL — builders cannot push or open PRs; the PM
#   pushes from the live checkout, or unsets the worktree pushurl first.
#   opencode and the verifier both run inside the worktree, so the human's working tree and
#   any uncommitted work are untouchable, parallel dispatches can't collide, and opencode's
#   per-directory session store makes --continue unambiguous per task. The worktree is left
#   in place — the PM inspects it, opens the PR from its branch, then removes it with
#   `git worktree remove <path>`. The worktree path is printed to stderr as
#   "[dispatch] worktree: <path>".
#
# Runs opencode on the task. If <verify-cmd> is given, it then verifies the working tree
# and self-corrects: on a failed verify it continues the SAME opencode session with the
# verifier output appended and re-verifies, up to <max-attempts> verify checks (default 3).
# This inner correction loop runs entirely in bash — no PM tokens are spent per iteration.
#
# Salvage: a builder exiting non-zero after self-committing completed work (clean tree, new
# commits since dispatch start) is NOT treated as a failure — the fallback is skipped and the
# committed state goes straight to the verifier.
#
# Exit codes (the PM branches on these):
#   0   success — the builder ran and, if a verifier was given, it passed
#   1   builder infra/model failure — could not produce a run even via the fallback model
#   20  verify never passed within max-attempts — code runs but is wrong -> PM escalates tier
#   21  --read-only violated — the run modified the target tree (changes left for inspection)
#   30  backend unavailable — CLI not installed/authenticated; PM skips to the next backend
#       in PROJECT.md builder_backends (no failure_count increment)
#
# Output capture: opencode assistant output stays on stdout. Full opencode + verifier logs
# go to a per-run logfile under logs/. On any non-zero exit the log tail is echoed to stderr
# so failures are never silent. The logfile path is always printed to stderr.
eval "$(~/.ssh/gh-agent-token.sh)"

READ_ONLY=false
WORKTREE_BRANCH=""
BACKEND=""
while true; do
  case "$1" in
    --read-only) READ_ONLY=true; shift ;;
    --worktree)  WORKTREE_BRANCH="$2"; shift 2 ;;
    --backend)   BACKEND="$2"; shift 2 ;;
    *) break ;;
  esac
done

MODEL="$1"
DIR="$2"
PROMPT_FILE="$3"
FALLBACK_MODEL="${4:-}"
VERIFY_CMD="${5:-}"
MAX_ATTEMPTS="${6:-3}"
TIER="${7:-}"   # optional: task tier (fast|standard|heavy), recorded in cost telemetry only

# --- [0c] Resolve the prompt file to an absolute path, once, before anything cds ---------
# Backends disagreed about what a relative <prompt-file> means: run_opencode_* and
# run_codex_fresh expand `$(cat "$PROMPT_FILE")` in the CALLER's cwd (they pass --dir/-C
# instead of cd'ing), while run_claude_* expand it inside `( cd "$DIR" && … )` — so the same
# relative argument resolved against the PM dir for two backends and against the target
# project dir for the third, where it failed silently. Same class as issue #11 (the critic's
# {{PLAN}} path). Resolve once, here, and the whole class is gone.
case "$PROMPT_FILE" in
  /*) ;;
  *)  _pf_dir="$(cd "$(dirname "$PROMPT_FILE")" 2>/dev/null && pwd)"
      [ -n "$_pf_dir" ] || { echo "[dispatch] prompt file directory not found: $(dirname "$PROMPT_FILE")" >&2; exit 2; }
      PROMPT_FILE="$_pf_dir/$(basename "$PROMPT_FILE")" ;;
esac
[ -r "$PROMPT_FILE" ] || { echo "[dispatch] prompt file not readable: $PROMPT_FILE" >&2; exit 2; }

# --- [0a] A build dispatch without a verifier is not a dispatch --------------------------
# Field data (307 runs across hometastic + gamedaytastic): 86% of gamedaytastic dispatches
# and 52% of hometastic's carried NO verify-cmd, so the self-correct loop never ran and the
# tier/backend escalation ladder — driven entirely by exit 20 — fired 4 times in 307 runs.
# Correctness silently moved into the orchestrator's context at peak cost. The verifier is
# cheap and mechanical; forgetting it is not recoverable after the fact, so refuse rather
# than warn. --read-only dispatches are exempt (they have no tree to verify).
if [ -z "$VERIFY_CMD" ] && ! $READ_ONLY && [ "${DISPATCH_ALLOW_NO_VERIFY:-0}" != "1" ]; then
  echo "[dispatch] refusing a build dispatch with no verify-cmd (arg 5)." >&2
  echo "           Pass the project's verify command from PROJECT.md § Verify command," >&2
  echo "           or set DISPATCH_ALLOW_NO_VERIFY=1 to override deliberately." >&2
  exit 2
fi
# tier is telemetry-only, so warn rather than refuse — but it was missing on 79% of runs,
# which is why cost-report.sh cannot attribute cost by tier today.
if [ -z "$TIER" ] && ! $READ_ONLY; then
  echo "[dispatch] warning: no tier (arg 7) — this run will not be attributable by tier in cost.jsonl." >&2
fi

# --- Backend selection (codex | claude | opencode) ---
infer_backend() {
  case "$1" in
    gpt-*|codex-*)                       echo codex ;;
    claude-*|sonnet|opus|haiku|fable)    echo claude ;;
    *)                                   echo opencode ;;
  esac
}
[ -z "$BACKEND" ] && BACKEND="$(infer_backend "$MODEL")"
case "$BACKEND" in codex|claude|opencode) ;; *)
  echo "[dispatch] unknown backend: $BACKEND" >&2; exit 1 ;;
esac
if ! command -v "$([ "$BACKEND" = opencode ] && echo opencode || echo "$BACKEND")" >/dev/null 2>&1; then
  echo "[dispatch] backend unavailable: $BACKEND CLI not on PATH" >&2
  exit 30
fi

# codex model slugs may carry an effort suffix (gpt-5.6-sol@low) — parsed per call so the
# fallback model can carry its own effort.
model_of()  { echo "${1%%@*}"; }
effort_of() { case "$1" in *@*) echo "${1##*@}" ;; *) echo "" ;; esac; }

# --- [0f/B4] Validate model slugs against MODELS.md before spending a dispatch -----------
# Four runs in the field died in 2-3 seconds on typo'd slugs — `deepseek/deepseek-v4-pro`
# and `deepseek-v4-pro` (missing the openrouter/ prefix), `openrouter/x-ai/grok-code-fast-1`
# (never in MODELS.md), `claude-sonnet-5` (MODELS.md defines sonnet = claude-sonnet-4.6).
# Each was logged as an ordinary task failure, so it could increment failure_count toward a
# Gate 2 that has nothing to do with the task. Exit 30 is the right code: "configuration
# problem, not task problem" — the PM skips to the next backend without touching
# failure_count. Under parallel fan-out a copy-pasted bad slug wastes N dispatches, not one.
# MODELS.md renders every slug in backticks in its tables; a missing MODELS.md skips the check.
MODELS_FILE="${MODELS_FILE:-$(dirname "$0")/MODELS.md}"
validate_model_slug() {
  local m; m="$(model_of "$1")"
  [ -n "$m" ] || return 0
  [ -r "$MODELS_FILE" ] || return 0          # framework copy missing — don't block the run
  grep -qF -- "\`$m\`" "$MODELS_FILE" && return 0
  echo "[dispatch] model slug '$m' is not listed in $MODELS_FILE." >&2
  echo "           Fix the slug in PLAN.md (a typo'd slug fails in seconds and pollutes failure_count)." >&2
  return 1
}
validate_model_slug "$MODEL" || exit 30
[ -n "$FALLBACK_MODEL" ] && { validate_model_slug "$FALLBACK_MODEL" || exit 30; }

# Logs anchor to the PM directory, not the caller's cwd: the task prompt always lives in
# <pm-dir>/prompts/, so default LOG_DIR to the prompts dir's sibling logs/. This keeps
# cost.jsonl and run logs in one place no matter where the orchestrator invokes dispatch
# from (e.g. a git worktree or any other cwd). OPENCODE_DISPATCH_LOG_DIR overrides.
if [ -n "${OPENCODE_DISPATCH_LOG_DIR:-}" ]; then
  LOG_DIR="$OPENCODE_DISPATCH_LOG_DIR"
else
  PROMPT_DIR="$(cd "$(dirname "$PROMPT_FILE")" 2>/dev/null && pwd)"
  LOG_DIR="${PROMPT_DIR:+$(dirname "$PROMPT_DIR")/logs}"
  LOG_DIR="${LOG_DIR:-logs}"
fi
mkdir -p "$LOG_DIR"
# Per-run uniqueness (issue #5): the second-granularity timestamp alone collides when the same
# prompt is dispatched twice within one second (tier escalation, retries) and the fixed
# verifier-output path collides across concurrent --worktree dispatches. Suffix the PID so the
# logfile — and every temp path derived from it below (verifier output, captured builder
# output, per-backend event/result files) — is unique per invocation.
LOG_FILE="${LOG_DIR}/$(basename "${PROMPT_FILE%.md}")-$(date +%Y%m%d-%H%M%S)-$$.log"
VERIFY_OUT="${LOG_FILE%.log}.verify.out"   # per-run, not the old shared ${LOG_DIR}/.verify.out
STALL_OUT="${LOG_FILE%.log}.lastout"       # captured builder stdout, used by the stall guard

# Active model — may switch to the fallback on an infra failure of the initial run.
ACTIVE_MODEL="$MODEL"

# Per-opencode-call wall-clock cap. A stalled model (e.g. a reasoning model burning its whole
# token budget on `reasoning` with no content/tool-call) otherwise hangs this call indefinitely
# — observed as a multi-hour no-op in logs/. On timeout, `timeout` exits 124, which flows through
# the same path as any non-zero opencode exit (fallback once, then a non-zero return to the PM),
# so a hang fails fast and cheap instead of stalling then needlessly escalating tiers. Override
# with OPENCODE_DISPATCH_TIMEOUT (seconds); 0 disables. No-op if `timeout` isn't on PATH.
DISPATCH_TIMEOUT="${OPENCODE_DISPATCH_TIMEOUT:-900}"
if [ "$DISPATCH_TIMEOUT" != "0" ] && command -v timeout >/dev/null 2>&1; then
  TIMEOUT_PREFIX=(timeout "$DISPATCH_TIMEOUT")
else
  TIMEOUT_PREFIX=()
fi

# --- Cost telemetry ---
# Append one structured record per dispatch to logs/cost.jsonl. Always-available signals
# (model, tier, attempts, verify result, exit, wall-clock) plus authoritative cost/token
# figures from opencode's local session store (~/.local/share/opencode/opencode.db): the
# `session` table records per-session cost (USD, as billed by OpenRouter) and token counts.
# We sum every session created in the target dir during this dispatch — that covers the
# primary run, --continue turns (same session), and a fallback-model rerun (second session).
# opencode's INFO logs do NOT carry usage, so the DB is the only reliable source.
# Never fatal — telemetry must not affect the dispatch exit code.
START_EPOCH="$(date +%s)"
ATTEMPTS_USED=1
# Primary-reliability telemetry: the cost record's `model` field is ACTIVE_MODEL (what actually
# did the work, for correct cost attribution), which flips to the fallback on a primary infra
# failure — so a primary that fails and is rescued is otherwise invisible in cost.jsonl. Record
# the originally-requested primary and whether it needed rescuing so cost-report.sh can compute a
# per-primary failure rate (the qwen3-coder-flash question — MODELS.md fast tier).
FALLBACK_USED=false        # true once ACTIVE_MODEL switches to the fallback model
STALL_RETRIES_USED=0       # count of silent-stall same-model retries the primary needed
OPENCODE_DB="${OPENCODE_DB:-$HOME/.local/share/opencode/opencode.db}"
DIR_ABS="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"

# --- Worktree isolation: builders never touch the live checkout ---
BUILDER_ENV=()
if [ -n "$WORKTREE_BRANCH" ]; then
  WT_ROOT="$(dirname "$DIR_ABS")/$(basename "$DIR_ABS")-worktrees"
  WT_PATH="${WT_ROOT}/$(basename "${PROMPT_FILE%.md}")"
  # If the branch is already checked out in some worktree (re-dispatch, review fixup on the
  # same PR branch under a different prompt name), reuse that path — `worktree add` would
  # hard-fail on an already-checked-out branch (issue #2).
  EXISTING_WT="$(git -C "$DIR_ABS" worktree list --porcelain 2>/dev/null \
    | awk -v b="branch refs/heads/${WORKTREE_BRANCH}" '/^worktree /{p=substr($0,10)} $0==b{print p; exit}')"
  if [ -n "$EXISTING_WT" ]; then
    WT_PATH="$EXISTING_WT"
  elif [ ! -d "$WT_PATH" ]; then
    mkdir -p "$WT_ROOT"
    if git -C "$DIR_ABS" show-ref --verify --quiet "refs/heads/${WORKTREE_BRANCH}"; then
      git -C "$DIR_ABS" worktree add "$WT_PATH" "$WORKTREE_BRANCH" >&2 \
        || { echo "[dispatch] worktree add failed (branch ${WORKTREE_BRANCH})" >&2; exit 1; }
    else
      git -C "$DIR_ABS" worktree add -b "$WORKTREE_BRANCH" "$WT_PATH" >&2 \
        || { echo "[dispatch] worktree add -b failed (branch ${WORKTREE_BRANCH})" >&2; exit 1; }
    fi
  fi
  DIR="$WT_PATH"
  DIR_ABS="$WT_PATH"
  echo "[dispatch] worktree: $WT_PATH (branch ${WORKTREE_BRANCH})" >&2

  # Builders must not push or open PRs (issue #3) — the PM owns PR opening (dispatch.md).
  # Enforce structurally, not by prompt: run opencode with gh unauthenticated (tokens unset,
  # GH_CONFIG_DIR pointed at an empty dir) and poison this worktree's pushurl so `git push`
  # fails fast. The pushurl is per-worktree config, so the live checkout and other worktrees
  # are unaffected; the PM pushes from the live checkout (`git -C <project-dir> push origin
  # <branch>`) or unsets it first (`git -C <wt> config --worktree --unset remote.origin.pushurl`).
  BUILDER_GH_DIR="$(mktemp -d)"
  BUILDER_ENV=(env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$BUILDER_GH_DIR")
  git -C "$WT_PATH" config extensions.worktreeConfig true 2>/dev/null || true
  git -C "$WT_PATH" config --worktree remote.origin.pushurl \
    "https://invalid.invalid/push-disabled-by-dispatch" 2>/dev/null \
    || echo "[dispatch] warning: could not poison pushurl; builder may be able to push" >&2
fi

# HEAD at dispatch start — used to detect builder self-commits when salvaging a run whose
# process exited non-zero after completing the work (issue #1).
HEAD_BEFORE="$(git -C "$DIR_ABS" rev-parse HEAD 2>/dev/null || true)"

emit_cost() {
  local code="$1" end_epoch dur verify_passed row cost_usd in_tok out_tok cache_tok reason_tok
  end_epoch="$(date +%s)"
  dur=$(( end_epoch - START_EPOCH ))
  if [ -z "$VERIFY_CMD" ]; then
    verify_passed=null   # no verifier configured — exit 0 means "opencode ran", not "verified"
  elif [ "$code" -eq 0 ]; then
    verify_passed=true
  else
    verify_passed=false
  fi
  cost_usd=null; in_tok=null; out_tok=null; cache_tok=null; reason_tok=null
  case "$BACKEND" in
    opencode)
      if command -v sqlite3 >/dev/null 2>&1 && [ -r "$OPENCODE_DB" ]; then
        row="$(sqlite3 -separator '|' "$OPENCODE_DB" \
          "select round(coalesce(sum(cost),0),6), coalesce(sum(tokens_input),0), coalesce(sum(tokens_output+tokens_reasoning),0), coalesce(sum(tokens_cache_read),0) \
           from session where directory='${DIR_ABS//\'/\'\'}' and time_created >= ${START_EPOCH}000;" 2>/dev/null)"
        if [ -n "$row" ]; then
          IFS='|' read -r cost_usd in_tok out_tok cache_tok <<< "$row"
        fi
      fi ;;
    codex)
      # Sum turn.completed usage across the fresh run and every resume turn. Subscription
      # lane: cost_usd stays null; tokens are the weekly-quota burn proxy (see MODELS.md).
      if [ -r "$CODEX_EVENTS" ]; then
        row="$(python3 -c "
import json,sys
i=o=c=r=0
for l in open(sys.argv[1]):
    try: e=json.loads(l)
    except Exception: continue
    if e.get('type')=='turn.completed':
        u=e.get('usage',{})
        i+=u.get('input_tokens',0); o+=u.get('output_tokens',0)
        c+=u.get('cached_input_tokens',0); r+=u.get('reasoning_output_tokens',0)
print(f'{i}|{o}|{c}|{r}')" "$CODEX_EVENTS" 2>/dev/null)"
        [ -n "$row" ] && IFS='|' read -r in_tok out_tok cache_tok reason_tok <<< "$row"
      fi ;;
    claude)
      # Subscription lane: sum usage from each result JSON; cost_usd stays null.
      if [ -r "$CLAUDE_RESULTS" ]; then
        row="$(python3 -c "
import json,sys
d=json.JSONDecoder(); s=open(sys.argv[1]).read(); i=o=c=0; pos=0
while pos < len(s):
    s2=s[pos:].lstrip()
    if not s2: break
    pos=len(s)-len(s2)
    try: obj,end=d.raw_decode(s,pos)
    except Exception: break
    pos=end
    u=obj.get('usage',{}) if isinstance(obj,dict) else {}
    i+=u.get('input_tokens',0); o+=u.get('output_tokens',0)
    c+=u.get('cache_read_input_tokens',0)
print(f'{i}|{o}|{c}')" "$CLAUDE_RESULTS" 2>/dev/null)"
        [ -n "$row" ] && IFS='|' read -r in_tok out_tok cache_tok <<< "$row"
      fi ;;
  esac
  printf '{"ts":"%s","role":"opencode","backend":"%s","prompt":"%s","model":"%s","primary_model":"%s","fallback_used":%s,"stall_retries":%s,"tier":%s,"attempts":%s,"verify_passed":%s,"exit":%s,"duration_s":%s,"cost_usd":%s,"input_tokens":%s,"output_tokens":%s,"cache_read_tokens":%s,"reasoning_tokens":%s,"log":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BACKEND" "$(basename "$PROMPT_FILE")" "$ACTIVE_MODEL" \
    "$MODEL" "$FALLBACK_USED" "${STALL_RETRIES_USED:-0}" \
    "$([ -n "$TIER" ] && printf '"%s"' "$TIER" || echo null)" \
    "${ATTEMPTS_USED:-1}" "$verify_passed" "$code" "$dur" \
    "${cost_usd:-null}" "${in_tok:-null}" "${out_tok:-null}" "${cache_tok:-null}" "${reason_tok:-null}" \
    "$(basename "$LOG_FILE")" >> "${LOG_DIR}/cost.jsonl" 2>/dev/null || true
}

run_opencode_fresh() {
  local model="$1"
  # Fresh session seeded with the task prompt file.
  "${TIMEOUT_PREFIX[@]}" "${BUILDER_ENV[@]}" opencode run \
    --model "$model" \
    --print-logs --log-level INFO \
    --dir "$DIR" \
    --dangerously-skip-permissions \
    "$(cat "$PROMPT_FILE")" \
    < /dev/null 2>> "$LOG_FILE"
}

run_opencode_continue() {
  local model="$1" message="$2"
  # Continue the most recent session in DIR with a new message. DIR is the task's own
  # worktree (--worktree), so opencode's per-directory session store makes "most recent
  # session in DIR" unambiguous even with many dispatches in flight.
  "${TIMEOUT_PREFIX[@]}" "${BUILDER_ENV[@]}" opencode run \
    --continue \
    --model "$model" \
    --print-logs --log-level INFO \
    --dir "$DIR" \
    --dangerously-skip-permissions \
    "$message" \
    < /dev/null 2>> "$LOG_FILE"
}

# --- codex backend ---
# JSONL events accumulate in $CODEX_EVENTS across the fresh run and every resume turn;
# emit_cost sums all turn.completed usage records. thread.started supplies the thread id
# used by `codex exec resume` for the verify feedback loop.
CODEX_EVENTS="${LOG_FILE%.log}.codex-events.jsonl"
CODEX_THREAD_ID=""

codex_effort_args() {
  local eff; eff="$(effort_of "$1")"
  [ -n "$eff" ] && printf -- '-c\nmodel_reasoning_effort=%s\n' "$eff"
}

codex_postrun() {
  # Accumulate this turn's events, capture the thread id, echo the last agent message to
  # stdout so the PM sees the builder's report like it does with opencode.
  # [0f/B5] The turn file used to be appended to BOTH $CODEX_EVENTS and $LOG_FILE and then
  # left on disk, so every codex run stored its full event JSONL three times. Measured in
  # the field: 221 orphaned .turn files, 25 MB of pure duplication, and a 91 MB logs/ dir.
  # The events file is the machine-readable copy; $LOG_FILE is meant to stay human-readable.
  # Set DISPATCH_KEEP_TURN_FILES=1 to retain the per-turn files while debugging a run.
  cat "$CODEX_EVENTS.turn" >> "$CODEX_EVENTS" 2>/dev/null
  # Builder's report goes to stdout (like opencode's assistant output).
  python3 -c "
import json,sys
for l in open(sys.argv[1]):
    try: e=json.loads(l)
    except Exception: continue
    if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message':
        print(e['item'].get('text',''))" "$CODEX_EVENTS.turn" 2>/dev/null || true
  local tid
  tid="$(python3 -c "
import json,sys
tid=''
for l in open(sys.argv[1]):
    try: e=json.loads(l)
    except Exception: continue
    if e.get('type')=='thread.started': tid=e.get('thread_id','')
print(tid)" "$CODEX_EVENTS.turn" 2>/dev/null || true)"
  [ -n "$tid" ] && CODEX_THREAD_ID="$tid"
  [ "${DISPATCH_KEEP_TURN_FILES:-0}" = "1" ] || rm -f "$CODEX_EVENTS.turn"
}

# Sandbox stays `workspace-write` (least privilege — no network-home-service widening), plus
# ONE narrow grant: the git directory as a writable_root. workspace-write blocks writes to the
# git store (index/refs/objects) — even when .git is inside the workspace — so a builder cannot
# `git commit` its own work; for a --worktree dispatch it's structurally worse, since the
# worktree only holds a gitdir *pointer* and the real store is the MAIN repo's .git, entirely
# outside the sandboxed workspace. Without this, codex produced correct-but-UNCOMMITTED work
# (observed 2026-07-15, hometastic T086/T087 — the orchestrator committed by hand). Granting the
# git common dir is sufficient and minimal: verified 2026-07-15 that a *signed* commit then
# succeeds (SSH signing reads the key FILE — no agent, no network). NOT full-access: codex still
# can't touch the home dir or reach services, and it doesn't need to — dispatch.sh runs the
# verify-cmd OUTSIDE the sandbox, so codex never has to run the build/test toolchain itself.
# `git --git-common-dir` resolves to the main .git for a worktree and to <repo>/.git otherwise;
# both need the grant. `exec resume` has no `-s` but accepts `-c`, so both paths pass it via -c.
# The git common dir is always granted (self-commit, above). CODEX_EXTRA_WRITABLE_ROOTS
# (issue #7) lets the PM add narrow extra writable roots per task so an in-sandbox verify can
# succeed where workspace-write otherwise denies the writes — for iOS/Xcode tasks that means
# the SwiftPM cache (e.g. ~/Library/Caches/org.swift.swiftpm) and the DerivedData root, passed
# as a colon-separated list of ABSOLUTE paths. Keep it least-privilege: name the specific cache
# dirs, not $HOME/Library. CAVEAT: CoreSimulatorService is a Mach service, NOT a filesystem
# path — it cannot be granted under workspace-write (only full-access, which is rejected here),
# so simulator-dependent tests still can't run inside the sandbox. This grant only unblocks
# compile / SwiftPM-cache / DerivedData writes so the builder can iterate against build errors;
# dispatch.sh's out-of-sandbox verify (run_verify) remains the correctness gate for sim tests.
codex_writable_roots_arg() {
  local gc roots=() r json
  gc="$(git -C "$DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  [ -n "$gc" ] && roots+=("$gc")
  if [ -n "${CODEX_EXTRA_WRITABLE_ROOTS:-}" ]; then
    # [0f/B1] Split on colon AND space. PROJECT.md documents this var as
    # "space/colon-separated" and its worked example uses a space, but this loop split on
    # ":" only — so a space-separated value became ONE path containing a space, a directory
    # that does not exist, and the grant silently did nothing. That is the grant issue #7
    # was closed to provide, so #7 may never have taken effect in the field; it is also the
    # most likely mechanical cause of issue #12 (builders substituting `swift parse` for a
    # real build because they could not write the SwiftPM cache / DerivedData).
    local IFS=': '
    for r in $CODEX_EXTRA_WRITABLE_ROOTS; do [ -n "$r" ] && roots+=("$r"); done
  fi
  [ ${#roots[@]} -eq 0 ] && return 0
  json="$(printf '"%s",' "${roots[@]}")"
  printf -- '-c\nsandbox_workspace_write.writable_roots=[%s]\n' "${json%,}"
}

# --- [0d / issue #10] First-event watchdog -----------------------------------------------
# `codex exec` reads stdin when stdin is not a TTY, to append to the prompt. Invoked from a
# non-interactive context whose stdin is an open pipe that never closes (Claude Code's Bash
# tool today; `ssh host "cmd"` under the remote-builder phase), codex blocks forever BEFORE
# the turn begins. The issue-#4 stall guard cannot see it: no event is ever emitted, and the
# OS considers the process actively reading rather than idle. Field evidence: a critic
# dispatch that ran 7200 s with 0 input and 0 output tokens, plus 5 zero-byte .turn files.
#
# Primary fix is `< /dev/null` on every codex invocation below. This watchdog is the belt to
# that suspenders: if no event has been written after CODEX_FIRST_EVENT_TIMEOUT seconds the
# turn never started, so kill it now instead of burning the full DISPATCH_TIMEOUT. Set the
# timeout to 0 to disable.
codex_watchdog() {
  local pid="$1" secs="${CODEX_FIRST_EVENT_TIMEOUT:-60}"
  [ "$secs" = "0" ] && return 0
  sleep "$secs"
  if kill -0 "$pid" 2>/dev/null && [ ! -s "$CODEX_EVENTS.turn" ]; then
    echo "[dispatch] codex emitted no events in ${secs}s — turn never began (issue #10); aborting run." >&2
    echo "[dispatch] --- codex first-event watchdog fired at $(date) ---" >> "$LOG_FILE"
    kill -TERM "$pid" 2>/dev/null
  fi
}

# Run a codex command in the background under the watchdog and return its exit code.
# Backgrounding is what makes the watchdog possible; `wait` preserves the exit status
# exactly as the foreground form did, so every caller's error handling is unchanged.
# CODEX_RUN_CWD (optional) — run the command from this directory. `exec` in the subshell
# means $! is codex's own pid, so the watchdog kills the right process. Redirections live
# here rather than at the call sites so the watchdog's own message still reaches the real
# stderr instead of being swallowed into $LOG_FILE.
codex_supervised() {
  : > "$CODEX_EVENTS.turn"
  ( if [ -n "${CODEX_RUN_CWD:-}" ]; then cd "$CODEX_RUN_CWD" || exit 1; fi; exec "$@" ) \
    < /dev/null > "$CODEX_EVENTS.turn" 2>> "$LOG_FILE" &
  local cpid=$!
  codex_watchdog "$cpid" &
  local wpid=$!
  wait "$cpid"; local ec=$?
  kill "$wpid" 2>/dev/null; wait "$wpid" 2>/dev/null
  return $ec
}

run_codex_fresh() {
  local spec="$1" args=()
  while IFS= read -r a; do args+=("$a"); done < <(codex_effort_args "$spec")
  while IFS= read -r a; do args+=("$a"); done < <(codex_writable_roots_arg)
  CODEX_RUN_CWD="" codex_supervised \
    "${TIMEOUT_PREFIX[@]}" "${BUILDER_ENV[@]}" codex exec \
    --json -C "$DIR" -s workspace-write --skip-git-repo-check \
    -c sandbox_workspace_write.network_access=true \
    -m "$(model_of "$spec")" "${args[@]}" \
    "$(cat "$PROMPT_FILE")"
  local ec=$?
  codex_postrun
  return $ec
}

run_codex_continue() {
  local spec="$1" message="$2" args=()
  [ -z "$CODEX_THREAD_ID" ] && { echo "[dispatch] codex: no thread id to resume" >&2; return 1; }
  while IFS= read -r a; do args+=("$a"); done < <(codex_effort_args "$spec")
  while IFS= read -r a; do args+=("$a"); done < <(codex_writable_roots_arg)
  # `exec resume` takes no -C/-s and runs in the CALLER's cwd (verified e2e 2026-07-15 —
  # it does NOT restore the thread's original cwd), so cd into the target dir explicitly.
  CODEX_RUN_CWD="$DIR" codex_supervised \
    "${TIMEOUT_PREFIX[@]}" "${BUILDER_ENV[@]}" codex exec resume "$CODEX_THREAD_ID" \
      --json --skip-git-repo-check \
      -c sandbox_workspace_write.network_access=true \
      -m "$(model_of "$spec")" "${args[@]}" \
      "$message"
  local ec=$?
  codex_postrun
  return $ec
}

# --- claude backend (subscription auth only — ANTHROPIC_API_KEY stripped) ---
# Result JSON (usage, session_id, total_cost_usd) accumulates in $CLAUDE_RESULTS; the
# session id feeds `claude -p --resume` for the verify feedback loop.
CLAUDE_RESULTS="${LOG_FILE%.log}.claude-results.jsonl"
CLAUDE_SESSION_ID=""

claude_postrun() {
  cat "$CLAUDE_RESULTS.turn" >> "$CLAUDE_RESULTS" 2>/dev/null
  cat "$CLAUDE_RESULTS.turn" >> "$LOG_FILE" 2>/dev/null
  python3 -c "
import json,sys
try: r=json.load(open(sys.argv[1]))
except Exception: sys.exit()
print(r.get('result',''))" "$CLAUDE_RESULTS.turn" 2>/dev/null || true
  local sid
  sid="$(python3 -c "
import json,sys
try: r=json.load(open(sys.argv[1]))
except Exception: sys.exit()
print(r.get('session_id',''))" "$CLAUDE_RESULTS.turn" 2>/dev/null || true)"
  [ -n "$sid" ] && CLAUDE_SESSION_ID="$sid"
}

run_claude_fresh() {
  local spec="$1"
  ( cd "$DIR" && "${TIMEOUT_PREFIX[@]}" "${BUILDER_ENV[@]}" env -u ANTHROPIC_API_KEY claude \
      -p --output-format json \
      --model "$(model_of "$spec")" \
      --dangerously-skip-permissions \
      "$(cat "$PROMPT_FILE")" ) \
    < /dev/null > "$CLAUDE_RESULTS.turn" 2>> "$LOG_FILE"
  local ec=$?
  claude_postrun
  return $ec
}

run_claude_continue() {
  local spec="$1" message="$2"
  [ -z "$CLAUDE_SESSION_ID" ] && { echo "[dispatch] claude: no session id to resume" >&2; return 1; }
  ( cd "$DIR" && "${TIMEOUT_PREFIX[@]}" "${BUILDER_ENV[@]}" env -u ANTHROPIC_API_KEY claude \
      -p --output-format json --resume "$CLAUDE_SESSION_ID" \
      --model "$(model_of "$spec")" \
      --dangerously-skip-permissions \
      "$message" ) \
    < /dev/null > "$CLAUDE_RESULTS.turn" 2>> "$LOG_FILE"
  local ec=$?
  claude_postrun
  return $ec
}

run_builder_fresh()    { "run_${BACKEND}_fresh" "$@"; }
run_builder_continue() { "run_${BACKEND}_continue" "$@"; }

# --- Silent-stall guard (issue #4) ---
# Intermittent OpenRouter/init stalls: the builder loads config, logs `init`, then produces
# NO assistant/tool output and exits non-zero ~60s later. The old flow then burned the
# fallback model on the same dead window (which stalled identically) and reported a whole
# cycle of zero work. Field evidence: an immediate re-dispatch of the SAME model succeeded.
# So: capture the fresh run's stdout (which for every backend is the builder's assistant
# report — empty means it never spoke), and when a run exits non-zero having produced no
# output, treat it as an infra stall and re-run the same model immediately, up to
# STALL_RETRIES times, before falling through to the fallback. A true no-exit hang is still
# bounded by DISPATCH_TIMEOUT (the `timeout` wrapper) above.
# The redirect+cat (not a pipe) keeps run_*_fresh in the current shell so the backend session
# state it sets (CODEX_THREAD_ID / CLAUDE_SESSION_ID, needed by the verify-resume loop) is
# preserved. Override count with OPENCODE_DISPATCH_STALL_RETRIES; 0 restores the old behavior.
STALL_RETRIES="${OPENCODE_DISPATCH_STALL_RETRIES:-1}"
run_fresh_stall_guarded() {
  local m="$1" tries=0 ec
  while :; do
    run_builder_fresh "$m" > "$STALL_OUT"; ec=$?
    cat "$STALL_OUT"   # pass the builder's report through to the PM on stdout, as before

    # The builder spoke: its exit code means what it says.
    [ -s "$STALL_OUT" ] && return "$ec"

    # [0f/B3] It produced NOTHING. Previously an exit 0 here returned success, and with no
    # verify-cmd configured dispatch.sh went straight to `finish 0` — so a run that did no
    # work at all was recorded as a completed task and the PM, which is told to branch on
    # the exit code, moved on. A silent run is an infra failure whatever it exited with:
    # retry the same model (field evidence: an immediate re-dispatch of the same model
    # succeeds), then report failure so the fallback and the PM error branch both engage.
    if [ "$tries" -lt "$STALL_RETRIES" ]; then
      tries=$((tries + 1))
      STALL_RETRIES_USED=$((STALL_RETRIES_USED + 1))
      echo "[dispatch] $m exited $ec with no output — infra/OpenRouter stall; immediate same-model retry ($tries/$STALL_RETRIES)." >&2
      echo "[dispatch] --- $m silent-stall retry $tries at $(date) ---" >> "$LOG_FILE"
      continue
    fi

    if [ "$ec" -eq 0 ]; then
      echo "[dispatch] $m exited 0 but produced no output — treating as an infra failure, not a completed task." >&2
      echo "[dispatch] --- $m empty-output exit 0 reclassified as failure at $(date) ---" >> "$LOG_FILE"
      return 1
    fi
    return "$ec"
  done
}

run_verify() {
  # Run the verifier in the target dir; tee combined output to the logfile. Returns its exit.
  echo "[dispatch] --- verify: $VERIFY_CMD ($(date)) ---" >> "$LOG_FILE"
  ( cd "$DIR" && bash -c "$VERIFY_CMD" ) > "$VERIFY_OUT" 2>&1
  local vec=$?
  cat "$VERIFY_OUT" >> "$LOG_FILE"
  return $vec
}

finish() {
  local code="$1"
  emit_cost "$code"
  if [ "$code" -ne 0 ]; then
    echo "[dispatch] failed (exit $code). Last 40 log lines:" >&2
    tail -n 40 "$LOG_FILE" >&2
  fi
  echo "[dispatch] full log: $LOG_FILE" >&2
  exit "$code"
}

# --- Read-only mode: snapshot the tree state so violations are detectable ---
# Snapshot = porcelain status (catches new/deleted/newly-modified files) + a hash of the
# full diff against HEAD (catches further edits to files that were ALREADY dirty, which
# leave the status line unchanged).
tree_state() {
  {
    git -C "$DIR_ABS" status --porcelain | sort
    git -C "$DIR_ABS" diff HEAD | shasum
    # Untracked, non-ignored files hashed by content (issue #6): status --porcelain lists them
    # by NAME only, so an in-place edit that keeps the same filename would otherwise leave the
    # snapshot unchanged. Ignored files (build products, DerivedData, node_modules) are
    # deliberately excluded to keep the snapshot practical — mutation of a gitignored file
    # remains an unenforced edge; lean on the backend's real sandbox for that case.
    ( cd "$DIR_ABS" && git ls-files --others --exclude-standard -z | sort -z \
        | xargs -0 -r shasum )
  } 2>/dev/null
}
if $READ_ONLY; then
  TREE_BEFORE="$(tree_state)"
fi

# A builder process exiting non-zero is a weak failure signal once commits exist (issue #1):
# models often self-commit completed work, then crash on an out-of-scope final step (e.g. a
# self-directed PR attempt). If the run left new commits and a clean tree, the work is
# plausibly complete — let the verify loop judge it instead of re-billing the whole task
# through the fallback (which would re-implement on top of the good commit).
salvageable_run() {
  $READ_ONLY && return 1   # a read-only run that committed is a violation, not a salvage
  local head_now
  head_now="$(git -C "$DIR_ABS" rev-parse HEAD 2>/dev/null)" || return 1
  [ -n "$HEAD_BEFORE" ] && [ "$head_now" != "$HEAD_BEFORE" ] || return 1
  [ -z "$(git -C "$DIR_ABS" status --porcelain 2>/dev/null)" ] || return 1
  return 0
}

# --- Initial run (silent-stall guard, then fallback on infra failure) ---
run_fresh_stall_guarded "$ACTIVE_MODEL"
if [ $? -ne 0 ]; then
  if salvageable_run; then
    echo "[dispatch] $ACTIVE_MODEL exited non-zero but left committed work and a clean tree — skipping fallback; verifying what's there." >&2
    echo "[dispatch] --- $ACTIVE_MODEL exit salvaged (commits present, tree clean) at $(date) ---" >> "$LOG_FILE"
  elif [ -n "$FALLBACK_MODEL" ]; then
    echo "[dispatch] Primary model ($ACTIVE_MODEL) failed. Retrying with fallback: $FALLBACK_MODEL" >&2
    echo "[dispatch] --- primary ($ACTIVE_MODEL) infra failure at $(date) ---" >> "$LOG_FILE"
    ACTIVE_MODEL="$FALLBACK_MODEL"
    FALLBACK_USED=true
    run_fresh_stall_guarded "$ACTIVE_MODEL" || { salvageable_run || finish 1; }
  else
    finish 1
  fi
fi

# --- Read-only mode: enforce that nothing changed; no verify loop ---
if $READ_ONLY; then
  TREE_AFTER="$(tree_state)"
  if [ "$TREE_BEFORE" != "$TREE_AFTER" ]; then
    echo "[dispatch] read-only violation — the run modified the target tree:" >&2
    diff <(echo "$TREE_BEFORE") <(echo "$TREE_AFTER") >&2 || true
    echo "[dispatch] changes left in place for inspection (not reverted)." >&2
    finish 21
  fi
  finish 0
fi

# --- No verifier configured: preserve legacy behavior (build/no-op check is the PM's job) ---
[ -z "$VERIFY_CMD" ] && finish 0

# --- Verify + self-correct loop ---
attempt=1
while true; do
  ATTEMPTS_USED="$attempt"
  if run_verify; then
    echo "[dispatch] verify passed on attempt $attempt/$MAX_ATTEMPTS." >&2
    finish 0
  fi

  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "[dispatch] verify still failing after $MAX_ATTEMPTS attempts — escalation needed." >&2
    finish 20
  fi

  echo "[dispatch] verify failed (attempt $attempt/$MAX_ATTEMPTS). Feeding error back to $ACTIVE_MODEL." >&2
  FEEDBACK="$(printf 'The verification command failed. Fix the code so it passes, then stop.\n\nCommand:\n%s\n\nOutput (tail):\n%s\n' \
    "$VERIFY_CMD" "$(tail -c 8000 "$VERIFY_OUT")")"

  run_builder_continue "$ACTIVE_MODEL" "$FEEDBACK" || finish 1
  attempt=$((attempt + 1))
done
