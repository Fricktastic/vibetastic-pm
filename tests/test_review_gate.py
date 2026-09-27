#!/usr/bin/env python3
"""Structured verdicts, round caps and the critique build gate (#18, #50)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review_gate  # noqa: E402

GATE = SCRIPTS / "review_gate.py"


def critic_reply(verdict="PROCEED", blocking_plan=0, pre=0, advisory=0, tier="R1"):
    return textwrap.dedent(f"""\
        VERDICT: {verdict}

        FINDINGS:
        - none

        <!-- CRITIC_RESULT_START -->
        ```yaml
        verdict: {verdict}
        blocking_plan: {blocking_plan}
        blocking_preexistent: {pre}
        advisory: {advisory}
        recommended_verify_tier: {tier}
        ```
        <!-- CRITIC_RESULT_END -->
        """)


def reviewer_reply(verdict="APPROVE", blockers=0, followups=0, notes=0):
    return textwrap.dedent(f"""\
        VERDICT: {verdict}
        <!-- REVIEWER_RESULT_START -->
        verdict: {verdict}
        blockers: {blockers}
        followups: {followups}
        notes: {notes}
        <!-- REVIEWER_RESULT_END -->
        """)


PLAN = textwrap.dedent("""\
    ---
    project: "fixture"
    stages:
      - id: 1
        name: "Implementation"
        status: in_progress
    tasks:
      - id: T010
        stage: 1
        title: "Risky change"
        agent: codex
        status: pending
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: true
        security: false
      - id: T010b
        stage: 1
        title: "Risky sub-task with a letter suffix"
        agent: codex
        status: pending
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: true
        security: false
      - id: T011
        stage: 1
        title: "UI tweak that needs a device check but no critique"
        agent: codex
        status: pending
        depends_on: []
        failure_count: 0
        verify_tier: R2
        risk: false
        security: false
      - id: T012
        stage: 1
        title: "Legacy task written before the risk flag"
        agent: codex
        status: pending
        depends_on: []
        failure_count: 0
        verify_tier: R1
      - id: T013
        stage: 1
        title: "Legacy pure-logic task"
        agent: codex
        status: pending
        depends_on: []
        failure_count: 0
        verify_tier: R0
      - id: T014
        stage: 1
        title: "Security wins over risk: false"
        agent: codex
        status: pending
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: false
        security: true
    ---
    """)


class ParseTests(unittest.TestCase):
    def test_critic_block_parses(self):
        parsed = review_gate.parse_result("critic", critic_reply("REWORK", 2, 1, 3, "R2"))
        self.assertEqual(parsed["verdict"], "REWORK")
        self.assertEqual(parsed["counts"], {"blocking_plan": 2, "blocking_preexistent": 1, "advisory": 3})
        self.assertEqual(parsed["recommended_verify_tier"], "R2")
        self.assertIsNone(parsed["problem"])

    def test_the_last_block_wins_and_template_echoes_are_malformed(self):
        template = ("<!-- CRITIC_RESULT_START -->\nverdict: PROCEED | REWORK\n"
                    "blocking_plan: <n>\n<!-- CRITIC_RESULT_END -->\n")
        self.assertEqual(review_gate.parse_result("critic", template)["verdict"], "MALFORMED")
        self.assertEqual(review_gate.parse_result("critic", template + critic_reply("PROCEED"))["verdict"], "PROCEED")

    def test_prose_verdict_without_block_is_malformed(self):
        parsed = review_gate.parse_result("critic", "VERDICT: PROCEED\nFINDINGS: none\n")
        self.assertEqual(parsed["verdict"], "MALFORMED")
        self.assertIn("no CRITIC_RESULT_START", parsed["problem"])

    def test_shipped_prompt_templates_match_the_parser(self):
        """The block each role prompt asks for must parse once its placeholders are filled."""
        import re
        for role, prompt in (("critic", "critic.md"), ("reviewer", "reviewer.md")):
            text = (ROOT / "prompts" / prompt).read_text()
            name = review_gate.BLOCK[role]
            block = re.search(r"<!-- " + name + r"_START -->.*?<!-- " + name + r"_END -->", text, re.S)
            self.assertIsNotNone(block, prompt)
            self.assertIn("{{PROJECT_POLICY}}", text, prompt)
            template = block.group(0)
            self.assertEqual(review_gate.parse_result(role, template)["verdict"], "MALFORMED",
                             "an echoed, unfilled template must never count as a verdict")
            allowed = review_gate.CRITIC_VERDICTS if role == "critic" else review_gate.REVIEWER_VERDICTS
            filled = re.sub(r"^verdict: <.*>$", "verdict: " + allowed[0], template, flags=re.M)
            filled = re.sub(r"^recommended_verify_tier: <.*>$", "recommended_verify_tier: R1", filled, flags=re.M)
            filled = re.sub(r"^(\w+): <number of .*>$", r"\1: 0", filled, flags=re.M)
            parsed = review_gate.parse_result(role, filled)
            self.assertIsNone(parsed["problem"], f"{prompt}: {parsed['problem']}")
            self.assertEqual(parsed["verdict"], allowed[0])

    def test_missing_count_is_malformed(self):
        text = "<!-- REVIEWER_RESULT_START -->\nverdict: APPROVE\nblockers: 0\n<!-- REVIEWER_RESULT_END -->"
        self.assertEqual(review_gate.parse_result("reviewer", text)["verdict"], "MALFORMED")
        self.assertEqual(review_gate.parse_result("reviewer", reviewer_reply("REJECT", 1))["verdict"], "REJECT")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pm = Path(self.temp.name)
        (self.pm / "PLAN.md").write_text(PLAN)
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def gate(self, *args, env=None):
        return subprocess.run([sys.executable, GATE, "--pm-dir", self.pm, *args],
                              capture_output=True, text=True, env=env)

    def record(self, role, task, text):
        self.counter += 1
        out = self.pm / f"out-{self.counter}.txt"
        out.write_text(text)
        result = self.gate("record", "--role", role, "--task", task, "--output", out,
                           "--run-id", f"run-{self.counter}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def ledger(self):
        return [json.loads(l) for l in (self.pm / "logs/verdicts.jsonl").read_text().splitlines()]

    def test_critic_cap_counts_rounds_but_not_error(self):
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T010").returncode, 0)
        self.record("critic", "T010", critic_reply("REWORK", 1))
        self.record("critic", "T010", critic_reply("ERROR"))          # config failure: no round
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T010").returncode, 0)
        self.record("critic", "T010", critic_reply("REWORK", 1))
        refused = self.gate("check-cap", "--role", "critic", "--task", "T010")
        self.assertEqual(refused.returncode, 31)
        self.assertIn("redesign", refused.stderr)
        self.assertEqual([r["round"] for r in self.ledger()], [1, None, 2])
        # Other tasks are unaffected.
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T012").returncode, 0)

    def test_malformed_output_still_uses_a_round(self):
        self.assertEqual(self.record("critic", "T010", "VERDICT: PROCEED"), "MALFORMED")
        self.record("critic", "T010", "no block at all")
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T010").returncode, 31)

    def test_project_cap_and_logged_override(self):
        (self.pm / "PROJECT.md").write_text("---\ncritic_round_cap: 1\n---\n")
        self.record("critic", "T010", critic_reply("REWORK", 1))
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T010").returncode, 31)
        self.assertEqual(self.gate("override-cap", "--role", "critic", "--task", "T010").returncode, 31,
                         "an override without a reason must be refused")
        granted = self.gate("override-cap", "--role", "critic", "--task", "T010", "--extra", "1",
                            "--reason", "operator: one more round after the re-spec")
        self.assertEqual(granted.returncode, 0, granted.stderr)
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T010").returncode, 0)
        self.record("critic", "T010", critic_reply("REWORK", 1))
        self.assertEqual(self.gate("check-cap", "--role", "critic", "--task", "T010").returncode, 31)

    def test_reviewer_fixup_cap(self):
        for _ in range(3):
            self.record("reviewer", "T013", reviewer_reply("REJECT", 1))
            self.assertEqual(self.gate("build-gate", "--task", "T013").returncode, 0)
        self.record("reviewer", "T013", reviewer_reply("APPROVE-WITH-FOLLOWUPS", 0, 2))
        # The review of fixup 3 may run (3 fixups used, cap 3) ...
        self.assertEqual(self.gate("check-cap", "--role", "reviewer", "--task", "T013").returncode, 0)
        self.record("reviewer", "T013", reviewer_reply("REJECT", 2))
        # ... but a fourth fixup build and a fifth review are refused.
        self.assertEqual(self.gate("build-gate", "--task", "T013").returncode, 31)
        self.assertEqual(self.gate("check-cap", "--role", "reviewer", "--task", "T013").returncode, 31)

    def test_build_gate_follows_risk_not_verify_tier(self):
        self.assertEqual(self.gate("build-gate", "--task", "T011").returncode, 0, "R2 + risk:false needs no critique")
        self.assertEqual(self.gate("build-gate", "--task", "T013").returncode, 0, "legacy R0 needs no critique")
        self.assertEqual(self.gate("build-gate", "--task", "T099").returncode, 0, "unknown task is not gated")
        for task in ("T010", "T012", "T014"):     # risk:true, legacy R1, security:true
            refused = self.gate("build-gate", "--task", task)
            self.assertEqual(refused.returncode, 31, task)
            self.assertIn("requires pre-build critique", refused.stderr)

    def test_adjudication_clears_the_gate_until_a_new_round(self):
        self.record("critic", "T010", critic_reply("REWORK", 1))
        refused = self.gate("adjudicate", "--task", "T010", "--outcome", "proceed")
        self.assertEqual(refused.returncode, 31)
        self.assertIn("BLOCKING-PLAN", refused.stderr)
        self.record("critic", "T010", critic_reply("PROCEED-WITH-CHANGES", 0, 1))
        self.assertEqual(self.gate("adjudicate", "--task", "T010", "--outcome", "proceed").returncode, 0)
        self.assertEqual(self.gate("build-gate", "--task", "T010").returncode, 0)
        self.gate("override-cap", "--role", "critic", "--task", "T010", "--reason", "re-spec")
        self.record("critic", "T010", critic_reply("REWORK", 1))
        self.assertEqual(self.gate("build-gate", "--task", "T010").returncode, 31,
                         "a newer critique round voids the older adjudication")

    def test_inconsistent_proceed_with_blocker_cannot_be_proceeded(self):
        self.record("critic", "T010", critic_reply("PROCEED", 1))
        self.assertEqual(self.gate("adjudicate", "--task", "T010", "--outcome", "proceed").returncode, 31)

    def test_operator_override_is_logged_and_needs_a_reason(self):
        self.assertEqual(self.gate("adjudicate", "--task", "T012", "--outcome", "override").returncode, 31)
        ok = self.gate("adjudicate", "--task", "T012", "--outcome", "override", "--reason",
                       "critiqued before the gate existed (TASK_LOG critic_returned 2026-09-20)")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.gate("build-gate", "--task", "T012").returncode, 0)
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["outcome"]), ("adjudication", "override"))

    def test_decisions_require_the_lease_in_a_managed_project(self):
        (self.pm / ".orchestrator").mkdir()
        (self.pm / ".orchestrator/config.json").write_text("{}")
        env = {k: v for k, v in os.environ.items() if k != "PM_ORCHESTRATOR_TOKEN"}
        refused = self.gate("adjudicate", "--task", "T012", "--outcome", "override", "--reason", "x", env=env)
        self.assertEqual(refused.returncode, 31)
        self.assertIn("lease", refused.stderr)
        self.assertFalse((self.pm / "logs/verdicts.jsonl").exists())


class DispatchGateTests(unittest.TestCase):
    """dispatch.sh end to end against a fake codex CLI (no network, no credentials)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.pm, self.code, self.bin = base / "pm", base / "code", base / "bin"
        for d in (self.pm / "prompts", self.code, self.bin):
            d.mkdir(parents=True)
        subprocess.run(["git", "-C", self.code, "init", "-q"], check=True)
        subprocess.run(["git", "-C", self.code, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        (self.pm / "PLAN.md").write_text(PLAN)
        (self.pm / "PROJECT.md").write_text(
            "---\nproject: x\n---\n\n## Risk triggers\n- touches the fixture-only trigger\n")
        self.reply = base / "reply.txt"
        self.calls = base / "calls"
        self.prompt_seen = base / "prompt-seen"
        fake = self.bin / "codex"
        fake.write_text(textwrap.dedent("""\
            #!/bin/bash
            printf 'call\\n' >> "$FAKE_CALLS"
            for last; do :; done
            printf '%s' "$last" > "$FAKE_PROMPT_SEEN"
            python3 -c 'import json,sys; print(json.dumps({"type":"thread.started","thread_id":"t"})); print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":open(sys.argv[1]).read()}})); print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}))' "$FAKE_REPLY"
            """))
        fake.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def dispatch(self, *args):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", PM_DIR=str(self.pm),
                   FAKE_CALLS=str(self.calls), FAKE_REPLY=str(self.reply),
                   FAKE_PROMPT_SEEN=str(self.prompt_seen), CODEX_FIRST_EVENT_TIMEOUT="0")
        env.pop("OPENCODE_DISPATCH_LOG_DIR", None)
        env.pop("PM_ORCHESTRATOR_TOKEN", None)
        return subprocess.run(["bash", ROOT / "dispatch.sh", *map(str, args)],
                              capture_output=True, text=True, env=env)

    def calls_made(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def critic(self, task="T010"):
        prompt = self.pm / f"prompts/critic-{task}.md"
        prompt.write_text("Critique the plan.\n\n{{PROJECT_POLICY}}\n")
        return self.dispatch("--read-only", "--role", "critic", "--author-model", "sonnet",
                             "--backend", "codex", "gpt-5.6-terra", self.code, prompt)

    def build(self, task="T010"):
        prompt = self.pm / f"prompts/task-{task}.md"
        prompt.write_text("Build it.\n")
        return self.dispatch("--worktree", f"task/{task}", "--backend", "codex", "gpt-5.6-terra",
                             self.code, prompt, "", "true", "1", "standard")

    def test_critic_rounds_are_recorded_and_capped_before_any_spend(self):
        self.reply.write_text(critic_reply("REWORK", 1))
        for expected_round in (1, 2):
            result = self.critic()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("critic verdict for T010: REWORK", result.stderr)
        # {{PROJECT_POLICY}} reached the role rendered with the project's own trigger.
        seen = self.prompt_seen.read_text()
        self.assertNotIn("{{PROJECT_POLICY}}", seen)
        self.assertIn("touches the fixture-only trigger", seen)
        rows = [json.loads(l) for l in (self.pm / "logs/verdicts.jsonl").read_text().splitlines()]
        self.assertEqual([(r["verdict"], r["round"]) for r in rows], [("REWORK", 1), ("REWORK", 2)])
        runs = [json.loads(l) for l in (self.pm / "logs/runs.jsonl").read_text().splitlines()]
        self.assertEqual([r.get("verdict") for r in runs if r["event"] == "run_finish"], ["REWORK", "REWORK"])
        self.assertTrue(all(r.get("pm_role") == "critic" and r.get("gate_task_id") == "T010"
                            for r in runs if r["event"] == "run_start"))
        before = self.calls_made()
        third = self.critic()
        self.assertEqual(third.returncode, 31, third.stderr)
        self.assertEqual(self.calls_made(), before, "a refused round must not invoke the backend")

    def test_role_run_without_a_task_is_refused(self):
        prompt = self.pm / "prompts/critique.md"
        prompt.write_text("x\n")
        result = self.dispatch("--read-only", "--role", "critic", "--author-model", "sonnet",
                               "--backend", "codex", "gpt-5.6-terra", self.code, prompt)
        self.assertEqual(result.returncode, 31)
        self.assertIn("--task", result.stderr)
        self.assertEqual(self.calls_made(), 0)

    def test_build_of_a_risk_task_waits_for_adjudication(self):
        self.reply.write_text("done\n")
        refused = self.build("T010")
        self.assertEqual(refused.returncode, 31, refused.stderr)
        self.assertEqual(self.calls_made(), 0)
        self.assertEqual(self.build("T011").returncode, 0, "risk:false builds without critique")
        self.reply.write_text(critic_reply("PROCEED"))
        self.assertEqual(self.critic().returncode, 0)
        adjudicated = subprocess.run([sys.executable, GATE, "--pm-dir", self.pm, "adjudicate",
                                      "--task", "T010", "--outcome", "proceed"],
                                     capture_output=True, text=True)
        self.assertEqual(adjudicated.returncode, 0, adjudicated.stderr)
        self.reply.write_text("done\n")
        allowed = self.build("T010")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("Do not stop", self.prompt_seen.read_text())

    def test_explicit_task_flag_keys_the_gate(self):
        prompt = self.pm / "prompts/fix-something.md"
        prompt.write_text("Build it.\n")
        result = self.dispatch("--task", "T010", "--worktree", "task/T010-b", "--backend", "codex",
                               "gpt-5.6-terra", self.code, prompt, "", "true", "1", "standard")
        self.assertEqual(result.returncode, 31, result.stderr)

    def test_suffixed_task_id_is_gated_not_truncated(self):
        # A prompt named for T010b must key the gate on T010b, not fall through as "T010"
        # or an unknown task (PR #56 review finding 1).
        self.reply.write_text("done\n")
        refused = self.build("T010b")
        self.assertEqual(refused.returncode, 31, refused.stderr)
        self.assertEqual(self.calls_made(), 0)
        self.reply.write_text(critic_reply("REWORK", 1))
        self.assertEqual(self.critic("T010b").returncode, 0)
        rows = [json.loads(l) for l in (self.pm / "logs/verdicts.jsonl").read_text().splitlines()]
        self.assertEqual({r["task_id"] for r in rows}, {"T010b"})

    def test_broken_gate_call_is_a_config_error_not_a_policy_stop(self):
        # review_gate.py rejecting its own invocation (exit 2) must not surface as exit 31,
        # which would send the operator to redesign/override/abort (review finding 2).
        prompt = self.pm / "prompts/fix-something.md"
        prompt.write_text("Build it.\n")
        result = self.dispatch("--task", "T010.1", "--worktree", "task/T010-c", "--backend", "codex",
                               "gpt-5.6-terra", self.code, prompt, "", "true", "1", "standard")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("configuration error", result.stderr)
        self.assertEqual(self.calls_made(), 0)


if __name__ == "__main__":
    unittest.main()
