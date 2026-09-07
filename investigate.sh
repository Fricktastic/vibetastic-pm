#!/bin/bash
# investigate — one command from "why is X happening?" to a cheap-tier read-only report.
#
# Why this exists (issue #42). Delegation in this framework happens where the flow forces it
# and almost nowhere else. Measured on gamedaytastic:
#
#   critic     104 dispatches  <- hard precondition for any R1+/security build
#   reviewer    24 dispatches  <- merge gate
#   diagnosis   10 dispatches  <- advice in pm-scope.md
#
# Over the same period the orchestrator personally read 467 target-repo source files (~304K
# tokens, 30.7% of everything it ingested). The lanes wired into a gate run constantly; the
# lane left to judgment does not, and the work it should have absorbed reappears as source
# reads at peak cost.
#
# Part of the reason is friction: dispatching an investigation meant hand-writing a prompt
# file, choosing a backend and tier, and assembling the dispatch.sh line — while reading the
# file was one tool call. This collapses that to one command, so the cheap path is also the
# easy one. It deliberately does NOT forbid reading anything; pm-scope.md's "the user asked
# you directly" exemption is real, and the orchestrator is also the human's thinking partner.
#
# Usage:
#   bash framework/investigate.sh <code-dir> "<question>" [context-file] [tier]
#
#   <code-dir>      target project (the same path you pass dispatch.sh)
#   <question>      what you want to know, in one sentence
#   [context-file]  optional file whose contents become "What you know already"
#   [tier]          fast | standard (default) | heavy
#
# Output: the report on stdout, plus the full log path on stderr. Read-only is enforced by
# dispatch.sh, so this can never modify the target project.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
CODE_DIR="${1:-}"
QUESTION="${2:-}"
CONTEXT_FILE="${3:-}"
TIER="${4:-standard}"

if [ -z "$CODE_DIR" ] || [ -z "$QUESTION" ]; then
  awk '/^# Usage:/{f=1} f&&!/^#/{exit} f{sub(/^# ?/,""); print}' "$0" >&2
  exit 2
fi
[ -d "$CODE_DIR" ] || { echo "[investigate] not a directory: $CODE_DIR" >&2; exit 2; }

case "$TIER" in fast|standard|heavy) ;; *)
  echo "[investigate] tier must be fast|standard|heavy (got '$TIER')" >&2; exit 2 ;;
esac

TEMPLATE="$HERE/prompts/investigator.md"
[ -r "$TEMPLATE" ] || { echo "[investigate] missing $TEMPLATE" >&2; exit 2; }

CONTEXT="none provided"
if [ -n "$CONTEXT_FILE" ]; then
  [ -r "$CONTEXT_FILE" ] || { echo "[investigate] cannot read context file: $CONTEXT_FILE" >&2; exit 2; }
  CONTEXT="$(cat "$CONTEXT_FILE")"
fi

# Render into the PM's prompts/ dir so the run log lands beside every other dispatch.
PM_DIR="$(dirname "$HERE")"
PROMPTS_DIR="$PM_DIR/prompts"
mkdir -p "$PROMPTS_DIR"
SLUG="$(printf '%s' "$QUESTION" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' \
        | sed 's/^-//; s/-$//' | cut -c1-40)"
PROMPT_FILE="$PROMPTS_DIR/investigate-${SLUG:-adhoc}-$(date +%H%M%S).md"

python3 -c "
import sys
tpl=open(sys.argv[1]).read()
tpl=tpl.replace('{{QUESTION}}', sys.argv[2]).replace('{{CONTEXT}}', sys.argv[3])
open(sys.argv[4],'w').write(tpl)
" "$TEMPLATE" "$QUESTION" "$CONTEXT" "$PROMPT_FILE" || {
  echo "[investigate] failed to render the prompt" >&2; exit 2; }

# Resolve the tier to a model the same way a build dispatch would — read the backend order
# from PROJECT.md, take the first entry, and let dispatch.sh's own tier/model check
# (issue #41) confirm the pairing rather than duplicating the table here.
BACKEND="$(sed -n '/^---$/,/^---$/p' "$PM_DIR/PROJECT.md" 2>/dev/null \
           | sed -n 's/^builder_backends:[[:space:]]*\[\([^,]*\).*/\1/p' | tr -d ' ' | head -1)"
BACKEND="${BACKEND:-codex}"
case "$BACKEND:$TIER" in
  codex:fast)        MODEL=gpt-5.6-luna ;;
  codex:standard)    MODEL=gpt-5.6-terra ;;
  codex:heavy)       MODEL=gpt-5.6-sol@low ;;
  claude:fast|claude:standard) MODEL=sonnet ;;
  claude:heavy)      MODEL=opus ;;
  opencode:fast)     MODEL=openrouter/deepseek/deepseek-v4-flash-0731 ;;
  opencode:heavy)    MODEL=openrouter/moonshotai/kimi-k2.6 ;;
  opencode:*)        MODEL=openrouter/minimax/minimax-m3 ;;
  *)                 MODEL=gpt-5.6-terra; BACKEND=codex ;;
esac

echo "[investigate] $BACKEND $MODEL ($TIER, read-only) -> $(basename "$PROMPT_FILE")" >&2
exec bash "$HERE/dispatch.sh" --read-only --backend "$BACKEND" "$MODEL" "$CODE_DIR" "$PROMPT_FILE"
