#!/bin/bash
# vibetastic-pm project setup — run once when standing up a <project>-pm/ directory.
# Usage: bash framework/setup.sh <project-name> <absolute-path-to-code-dir> <org/repo>
#
# Writes:
#   .claude/settings.json   — allowlist for cross-directory operations
#   PROJECT.md              — project paths for the partner-orchestrator

set -e

if [ $# -lt 3 ] || [ $# -gt 4 ]; then
  echo "Usage: bash framework/setup.sh <project-name> <absolute-path-to-code-dir> <org/repo> [verify-cmd]"
  echo "Example: bash framework/setup.sh hometastic /Users/tim/Developer/hometastic-code Fricktastic/hometastic-code 'swift build'"
  echo "verify-cmd: single-line command, run in the code dir, exit 0 = change didn't break the project."
  echo "            Powers dispatch.sh's self-correction loop. Omit to disable (loop degrades to single run)."
  exit 1
fi

PROJECT_NAME="$1"
CODE_DIR="$2"
ISSUE_REPO="$3"
TEST_CMD="${TEST_CMD:-}"
VERIFY_CMD="${4:-}"
PM_DIR="$(pwd)"

if [ ! -d "$CODE_DIR" ]; then
  echo "Error: code directory does not exist: $CODE_DIR"
  exit 1
fi

# Write .claude/settings.json
mkdir -p .claude
cat > .claude/settings.json <<EOF
{
  "permissions": {
    "allow": [
      "Bash(*)",
      "Read(${CODE_DIR}/**)",
      "Edit(${CODE_DIR}/**)",
      "Write(${CODE_DIR}/**)",
      "Read(${PM_DIR}/**)",
      "Edit(${PM_DIR}/**)",
      "Write(${PM_DIR}/**)"
    ],
    "deny": [
      "mcp__sosumi__fetchAppleDocumentation",
      "mcp__sosumi__fetchAppleVideoTranscript",
      "mcp__sosumi__fetchExternalDocumentation",
      "mcp__sosumi__searchAppleDocumentation",
      "mcp__claude_ai_Figma__add_code_connect_map",
      "mcp__claude_ai_Figma__create_new_file",
      "mcp__claude_ai_Figma__generate_diagram",
      "mcp__claude_ai_Figma__get_code_connect_map",
      "mcp__claude_ai_Figma__get_code_connect_suggestions",
      "mcp__claude_ai_Figma__get_context_for_code_connect",
      "mcp__claude_ai_Figma__get_design_context",
      "mcp__claude_ai_Figma__get_figjam",
      "mcp__claude_ai_Figma__get_libraries",
      "mcp__claude_ai_Figma__get_metadata",
      "mcp__claude_ai_Figma__get_screenshot",
      "mcp__claude_ai_Figma__get_variable_defs",
      "mcp__claude_ai_Figma__search_design_system",
      "mcp__claude_ai_Figma__send_code_connect_mappings",
      "mcp__claude_ai_Figma__upload_assets",
      "mcp__claude_ai_Figma__use_figma",
      "mcp__claude_ai_Figma__whoami"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PM_DIR}/framework/scripts/spec-body-guard.py\""
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PM_DIR}/framework/scripts/log-agent-spawn.py\""
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PM_DIR}/framework/scripts/plan-lint-hook.py\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${PM_DIR}/framework/scripts/log-partner-burn.py\""
          }
        ]
      }
    ]
  }
}
EOF

echo "✓ Wrote .claude/settings.json"

# Write PROJECT.md
# Builder backend order: flat-rate subscriptions first, metered OpenRouter overflow last
# (MODELS.md → Builder Backends). Filtered to CLIs actually installed on this machine.
BACKENDS=""
for be in codex claude opencode; do
  command -v "$be" >/dev/null 2>&1 && BACKENDS="${BACKENDS:+$BACKENDS, }$be"
done
BACKENDS="${BACKENDS:-opencode}"

cat > PROJECT.md <<EOF
---
project: ${PROJECT_NAME}
setup_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
builder_backends: [${BACKENDS}]
reviewer_backends: [opencode]
critic_backends: [codex, opencode]
codex_weekly_burn_threshold: 4000000
claude_window_burn_threshold: null   # ungated until telemetry supports a number (issue #14)
max_concurrent: {codex: 2, claude_builder: 1, claude_review: 2, opencode: 4}
max_concurrent_total: 6
---

## Project Paths

| Key | Path |
|-----|------|
| PM directory | \`${PM_DIR}\` |
| Code directory | \`${CODE_DIR}\` |
| Issue repo | \`${ISSUE_REPO}\` |

## Verify command

<!-- Single-line command run in the code directory after each OpenCode task. Exit 0 = the
     task didn't break the project. Passed to dispatch.sh as the verifier; on failure the loop
     feeds its output back to the model and retries. Leave the code block empty to disable.

     It must COMPILE THE TEST TARGET, not just the app — on iOS use
     `xcodebuild build-for-testing`, never a bare `build` (issue #36).

     THIS IS NOT A TEST RUN. Builders cannot execute simulator-dependent tests
     (CoreSimulatorService is a Mach service no sandbox grant provides). Running the suite is
     the orchestrator's job, on a real simulator/device — framework/VERIFY.md § Who runs what. -->

\`\`\`
${VERIFY_CMD}
\`\`\`

## Test command

<!-- Single-line command that RUNS the suite on a real simulator/device, from the code
     directory. This is the orchestrator's command, not the builder's — dispatch.sh never
     runs it. It lives here so that every session has one canonical answer to "how do I run
     the tests" instead of re-deriving it from scratch (issue #37: 27 of 35 gamedaytastic
     partner transcripts re-derived the sandbox/test story independently).

     Pin the destination explicitly — an unpinned destination picks a different simulator per
     machine and per Xcode update. Leave empty if the project has no runnable suite. -->

\`\`\`
${TEST_CMD}
\`\`\`

## Notes

<!-- Add any project-specific notes here for future PM sessions. -->
EOF

echo "✓ Wrote PROJECT.md"
echo ""
echo "Setup complete. This -pm directory holds project state, logs, and artifacts, and is"
echo "where you launch the orchestrator (A1 model): run 'claude' here and the partner drives"
echo "framework/dispatch.sh against this directory and enforces framework/VERIFY.md as the"
echo "merge gate. See framework/CLAUDE.md."
