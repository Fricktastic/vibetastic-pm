#!/usr/bin/env python3
"""The merge gate pins the verified tree and requires a fail-on-base observation (#35)."""

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

import merge_gate  # noqa: E402
import review_gate  # noqa: E402

GATE = SCRIPTS / "merge_gate.py"
REVIEW_GATE = SCRIPTS / "review_gate.py"

PLAN = textwrap.dedent("""\
    ---
    project: "fixture"
    stages:
      - id: 1
        name: "Implementation"
        status: in_progress
    tasks:
      - id: T020
        stage: 1
        title: "Fix the ordering bug (test-observable)"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: false
        security: false
        observation: test
        observation_cmd: "bash tests/check.sh   # the named regression test"
      - id: T021
        stage: 1
        title: "Device-only change"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R2
        risk: false
        observation: runtime
      - id: T022
        stage: 1
        title: "Test-only task"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: false
        observation: none
      - id: T023
        stage: 1
        title: "Legacy task written before #35"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R1
        risk: false
      - id: T024
        stage: 1
        title: "Typo in the observation kind"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: false
        observation: tests
      - id: T025
        stage: 1
        title: "Written from the new template, observation never decided"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: false
        observation: null   # test|runtime|none
      - id: T026
        stage: 1
        title: "Security-sensitive change"
        agent: codex
        status: in_progress
        depends_on: []
        failure_count: 0
        verify_tier: R0
        risk: true
        security: true
        observation: none
      - id: T027
        stage: 1
        title: "Design pass"
        agent: designer
        status: in_progress
        depends_on: []
        failure_count: 0
        observation: none
    ---
    """)

# tests/check.sh on the base tree is an inert placeholder that passes. The branch replaces it
# with the real regression test. The fail-on-base run is only red on base BECAUSE the branch's
# test is overlaid — so the happy path also proves the overlay happens.
BASE_CHECK = "exit 0\n"
REAL_CHECK = "grep -q fixed src/app.txt\n"


def reviewer_reply(verdict="APPROVE", blockers=0):
    return textwrap.dedent(f"""\
        VERDICT: {verdict}
        <!-- REVIEWER_RESULT_START -->
        verdict: {verdict}
        blockers: {blockers}
        followups: 0
        notes: 0
        <!-- REVIEWER_RESULT_END -->
        """)


class MergeGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.pm, self.code = base / "pm", base / "code"
        self.pm.mkdir()
        self.code.mkdir()
        (self.pm / "PLAN.md").write_text(PLAN)
        (self.pm / "PROJECT.md").write_text("---\nproject: x\n---\n\n## Test command\n\n```\nbash tests/check.sh\n```\n")
        self.git("init", "-q", "-b", "main")
        self.write("src/app.txt", "buggy\n")
        self.write("tests/check.sh", BASE_CHECK)
        self.commit("base")
        self.git("checkout", "-q", "-b", "task/T020")
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    # --- helpers ----------------------------------------------------------------------------

    def git(self, *args):
        return subprocess.run(["git", "-C", self.code, "-c", "user.email=t@t", "-c", "user.name=t", *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def write(self, path, text):
        target = self.code / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def gate(self, *args, env=None):
        return subprocess.run([sys.executable, GATE, "--pm-dir", self.pm, *map(str, args)],
                              capture_output=True, text=True, env=env)

    def review(self, task="T020", verdict="APPROVE", blockers=0, sha=None, clean="true"):
        self.counter += 1
        out = self.pm / f"review-{self.counter}.txt"
        out.write_text(reviewer_reply(verdict, blockers))
        result = subprocess.run([sys.executable, REVIEW_GATE, "--pm-dir", self.pm, "record",
                                 "--role", "reviewer", "--task", task, "--output", out,
                                 "--head-sha", sha or self.git("rev-parse", "HEAD"),
                                 "--tree-clean", clean], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def check(self, task="T020", *extra):
        return self.gate("check", "--task", task, "--dir", self.code, "--base", "main", *extra)

    def ledger(self):
        return [json.loads(l) for l in (self.pm / "logs/verdicts.jsonl").read_text().splitlines()]

    def fix_with_test(self):
        self.write("src/app.txt", "fixed\n")
        self.commit("the fix")
        self.write("tests/check.sh", REAL_CHECK)
        return self.commit("regression test")

    def full_evidence(self, task="T020", cmd=None):
        extra = ("--cmd", cmd) if cmd else ()
        self.assertEqual(self.gate("verify", "--task", task, "--dir", self.code, *extra).returncode, 0)
        self.review(task)

    def checks(self, result):
        return {k: v["status"] for k, v in json.loads(result.stdout)["checks"].items()}

    # --- the happy path ---------------------------------------------------------------------

    def test_pinned_evidence_at_head_allows_the_merge(self):
        sha = self.fix_with_test()
        self.full_evidence()
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        self.assertEqual(fob.returncode, 0, fob.stderr)
        row = json.loads(fob.stdout)
        self.assertEqual(row["sha"], sha)
        self.assertEqual(row["overlaid"], ["tests/check.sh"])
        self.assertNotEqual(row["base_exit"], 0)
        self.assertEqual(row["branch_exit"], 0)
        self.assertEqual(row["cmd"], "bash tests/check.sh   # the named regression test")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.checks(result), {"verification": "pass", "review": "pass",
                                               "production_diff": "pass", "observation": "pass",
                                               "security": "skipped"})
        self.assertEqual(self.ledger()[-1]["event"], "merge_check")
        self.assertTrue(self.ledger()[-1]["allowed"])
        # The temporary base worktree is gone.
        self.assertNotIn("merge-gate-", self.git("worktree", "list"))

    # --- issue #35: the T078 shape ----------------------------------------------------------

    def test_t078_a_commit_after_verification_that_reverts_the_fix_is_refused(self):
        self.fix_with_test()
        self.full_evidence()
        self.assertEqual(self.gate("fail-on-base", "--task", "T020", "--dir", self.code,
                                   "--base", "main").returncode, 0)
        self.assertEqual(self.check().returncode, 0)
        # "guard ordering-test slices so a reverted fix fails cleanly" — and reverts the fix.
        self.write("src/app.txt", "buggy\n")
        self.write("tests/check.sh", REAL_CHECK + "# guard slices\n")
        self.commit("guard ordering-test slices")
        result = self.check()
        self.assertEqual(result.returncode, 31, result.stdout + result.stderr)
        self.assertEqual(self.checks(result), {"verification": "fail", "review": "fail",
                                               "production_diff": "fail", "observation": "fail",
                                               "security": "skipped"})
        self.assertIn("HEAD moved after verification", result.stderr)
        self.assertIn("guard ordering-test slices", result.stderr)   # names the commits since
        self.assertIn("zero production change", result.stderr)
        # Re-running the evidence on the new head cannot rescue it: the test fails on the branch.
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        self.assertEqual(fob.returncode, 1)
        self.assertIn("FAILS on the branch", fob.stderr)

    def test_net_diff_is_what_counts_not_the_commits(self):
        self.fix_with_test()
        self.write("src/app.txt", "buggy\n")
        self.commit("revert the fix")
        result = self.check("T020")
        self.assertEqual(self.checks(result)["production_diff"], "fail")
        self.assertIn("tests/check.sh", result.stderr)

    # --- fail-on-base -----------------------------------------------------------------------

    def test_an_inert_test_that_passes_on_base_is_refused(self):
        self.write("src/app.txt", "fixed\n")
        self.write("tests/check.sh", "exit 0   # asserts nothing\n")
        self.commit("fix + a test that observes nothing")
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        self.assertEqual(fob.returncode, 1)
        self.assertIn("PASSES on the base tree", fob.stderr)
        self.full_evidence()
        self.assertEqual(self.checks(self.check())["observation"], "fail")

    def test_no_changed_test_is_a_policy_stop(self):
        self.write("src/app.txt", "fixed\n")
        self.commit("fix without a test")
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        self.assertEqual(fob.returncode, 31)
        self.assertIn("changes no test path", fob.stderr)

    def test_expect_fail_pattern_distinguishes_a_harness_failure(self):
        self.fix_with_test()
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main",
                        "--expect-fail-pattern", "AssertionError")
        self.assertEqual(fob.returncode, 1)
        self.assertIn("not with --expect-fail-pattern", fob.stderr)
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main",
                        "--cmd", "bash tests/check.sh || { echo AssertionError; exit 1; }",
                        "--expect-fail-pattern", "AssertionError")
        self.assertEqual(fob.returncode, 0, fob.stderr)

    def test_deleted_test_files_are_removed_from_the_base_run(self):
        self.write("tests/old_test.sh", "exit 0\n")
        self.git("checkout", "-q", "main")
        self.commit("an old test on main")
        self.git("checkout", "-q", "task/T020")
        self.git("rebase", "-q", "main")
        self.fix_with_test()
        self.git("rm", "-q", "tests/old_test.sh")
        self.commit("retire the old test")
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main",
                        # With the retired test still present, this "suite" passes on base —
                        # so it only fails there if the removal really happened.
                        "--cmd", "test -e tests/old_test.sh && exit 0; bash tests/check.sh")
        row = json.loads(fob.stdout)
        self.assertEqual(row["removed"], ["tests/old_test.sh"])
        self.assertEqual(fob.returncode, 0, fob.stderr)

    # --- pinning ----------------------------------------------------------------------------

    def test_a_commit_made_while_the_suite_runs_verifies_nothing(self):
        self.fix_with_test()
        result = self.gate("verify", "--task", "T020", "--dir", self.code, "--cmd",
                           "git -c user.email=t@t -c user.name=t commit -q --allow-empty -m sneaky")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads(result.stdout)["passed"])
        self.assertIn("HEAD moved while the suite ran", result.stderr)

    def test_a_dirty_tree_cannot_be_verified(self):
        self.fix_with_test()
        self.write("src/app.txt", "fixed but uncommitted\n")
        result = self.gate("verify", "--task", "T020", "--dir", self.code)
        self.assertEqual(result.returncode, 31)
        self.assertIn("uncommitted or untracked", result.stderr)
        self.assertFalse((self.pm / "logs/verdicts.jsonl").exists())

    def test_review_must_be_at_head_approving_and_clean(self):
        old = self.fix_with_test()
        self.gate("verify", "--task", "T020", "--dir", self.code)
        self.review(sha=old, clean="false")
        self.assertIn("uncommitted changes", self.check().stderr)
        self.review(verdict="REJECT", blockers=1)
        self.assertIn("REJECT", self.check().stderr)
        self.review()
        self.assertEqual(self.checks(self.check())["review"], "pass")
        self.write("tests/check.sh", REAL_CHECK + "# nit\n")
        self.commit("post-review tweak")
        self.assertIn(f"the latest review read {old[:12]}", self.check().stderr)

    def test_review_recorded_before_pinning_needs_a_rereview(self):
        self.fix_with_test()
        out = self.pm / "legacy-review.txt"
        out.write_text(reviewer_reply())
        subprocess.run([sys.executable, REVIEW_GATE, "--pm-dir", self.pm, "record", "--role", "reviewer",
                        "--task", "T020", "--output", out], check=True, capture_output=True)
        self.assertIn("before tree pinning", self.check().stderr)

    def test_every_verified_rung_must_pass_at_the_candidate(self):
        self.fix_with_test()
        self.review()
        self.gate("verify", "--task", "T020", "--dir", self.code, "--cmd", "true", "--label", "integration")
        self.write("tests/check.sh", REAL_CHECK + "# more\n")
        self.commit("more tests")
        self.review()
        self.gate("verify", "--task", "T020", "--dir", self.code)          # suite only
        result = self.check()
        self.assertEqual(self.checks(result)["verification"], "fail")
        self.assertIn("integration: HEAD moved", result.stderr)
        # A flake: passes, then fails at the same commit. Latest wins.
        self.gate("verify", "--task", "T020", "--dir", self.code, "--cmd", "true", "--label", "integration")
        self.assertEqual(self.checks(self.check())["verification"], "pass")
        self.gate("verify", "--task", "T020", "--dir", self.code, "--cmd", "false", "--label", "integration")
        self.assertIn("integration: failed at", self.check().stderr)

    # --- observation kinds and the legacy default -------------------------------------------

    def test_runtime_observation_is_pinned_to_the_commit(self):
        self.write("src/app.txt", "fixed\n")
        old = self.commit("device-visible fix")
        self.full_evidence("T021")
        artifact = self.pm / "shot.png"
        artifact.write_bytes(b"png")
        missing = self.gate("observe", "--task", "T021", "--dir", self.code, "--evidence", " ",
                            "--summary", "x")
        self.assertEqual(missing.returncode, 31)
        observed = self.gate("observe", "--task", "T021", "--dir", self.code, "--evidence", artifact,
                             "--summary", "cold launch x5 on the simulator; banner shows the fix")
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertIn("evidence_sha256", json.loads(observed.stdout))
        self.assertEqual(self.check("T021").returncode, 0, self.check("T021").stderr)
        self.write("src/app.txt", "fixed again\n")
        self.commit("follow-up")
        self.gate("verify", "--task", "T021", "--dir", self.code)
        self.review("T021")
        result = self.check("T021")
        self.assertEqual(self.checks(result)["observation"], "fail")
        self.assertIn("no runtime observation recorded", result.stderr)
        self.assertNotEqual(old, self.git("rev-parse", "HEAD"))

    def test_observation_none_allows_a_test_only_diff(self):
        self.write("tests/check.sh", "exit 0  # clearer\n")
        self.commit("test hygiene")
        self.full_evidence("T022")
        result = self.check("T022")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.checks(result)["observation"], "skipped")
        # ...but the same diff under a task that claims a behaviour change is refused.
        self.full_evidence("T021")
        self.assertEqual(self.checks(self.check("T021"))["production_diff"], "fail")

    def test_legacy_task_skips_the_observation_but_keeps_the_pin(self):
        self.write("src/app.txt", "fixed\n")
        self.commit("legacy fix")
        self.full_evidence("T023")
        result = self.check("T023")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.checks(result)["observation"], "skipped")
        self.assertIn("pre-#35", result.stderr)
        self.write("src/app.txt", "buggy\n")
        self.commit("revert")
        self.assertEqual(self.check("T023").returncode, 31)

    def test_unknown_observation_kind_is_refused(self):
        self.write("src/app.txt", "fixed\n")
        self.commit("fix")
        self.full_evidence("T024")
        result = self.check("T024")
        self.assertEqual(self.checks(result)["observation"], "fail")
        self.assertIn("'tests' is not one of", result.stderr)

    def test_a_null_observation_is_undecided_not_legacy(self):
        self.write("src/app.txt", "fixed\n")
        self.commit("fix")
        self.full_evidence("T025")
        result = self.check("T025")
        self.assertEqual(self.checks(result)["observation"], "fail")
        self.assertIn("observation: is unset", result.stderr)

    # --- escape hatch -----------------------------------------------------------------------

    def test_override_is_per_check_per_commit_and_needs_a_reason(self):
        self.write("tests/check.sh", REAL_CHECK)
        self.commit("test-only change filed as a fix")
        self.full_evidence("T021", cmd="true")
        self.gate("observe", "--task", "T021", "--dir", self.code, "--evidence", "log line 12",
                  "--summary", "seen")
        self.assertEqual(self.check("T021").returncode, 31)
        no_reason = self.gate("override", "--task", "T021", "--dir", self.code,
                              "--check", "production_diff")
        self.assertEqual(no_reason.returncode, 31)
        self.gate("override", "--task", "T021", "--dir", self.code, "--check", "production_diff",
                  "--reason", "operator: the fix shipped in T019; this task only adds the test")
        result = self.check("T021")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.checks(result)["production_diff"], "overridden")
        self.write("tests/check.sh", REAL_CHECK + "# again\n")
        self.commit("another commit")
        self.full_evidence("T021", cmd="true")
        self.gate("observe", "--task", "T021", "--dir", self.code, "--evidence", "log", "--summary", "seen")
        self.assertEqual(self.checks(self.check("T021"))["production_diff"], "fail",
                         "an override must not carry to a new commit")

    def test_decisions_require_the_lease_in_a_managed_project(self):
        self.fix_with_test()
        (self.pm / ".orchestrator").mkdir()
        (self.pm / ".orchestrator/config.json").write_text("{}")
        env = dict(os.environ)
        env.pop("PM_ORCHESTRATOR_TOKEN", None)
        for args in (("observe", "--evidence", "x", "--summary", "y"),
                     ("override", "--check", "review", "--reason", "r")):
            result = self.gate(args[0], "--task", "T020", "--dir", self.code, *args[1:], env=env)
            self.assertEqual(result.returncode, 31, result.stderr)
            self.assertIn("lease", result.stderr)

    # --- merge: GitHub enforces the pin at the moment of merge ------------------------------

    def test_merge_passes_the_verified_sha_to_gh_and_never_calls_it_on_refusal(self):
        fake_bin = Path(self.temp.name) / "bin"
        fake_bin.mkdir()
        calls = Path(self.temp.name) / "gh-calls"
        gh = fake_bin / "gh"
        gh.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{calls}"\npwd -P > "{calls}.cwd"\n')
        gh.chmod(0o755)
        env = dict(os.environ, MERGE_GATE_GH=str(gh))
        sha = self.fix_with_test()
        refused = self.gate("merge", "--task", "T020", "--dir", self.code, "--base", "main",
                            "--pr", "45", "--repo", "org/app", env=env)
        self.assertEqual(refused.returncode, 31)
        self.assertFalse(calls.exists(), "a refused merge must never reach gh")
        self.full_evidence()
        self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        merged = self.gate("merge", "--task", "T020", "--dir", self.code, "--base", "main",
                           "--pr", "45", "--repo", "org/app", "--", "--squash", env=env)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertEqual(calls.read_text().strip(),
                         f"pr merge 45 --match-head-commit {sha} --repo org/app --squash")
        self.assertEqual(self.ledger()[-1]["event"], "merge")
        # gh runs in the verified checkout, never the PM directory's own repo.
        self.assertEqual(Path(str(calls) + ".cwd").read_text().strip(),
                         str(Path(self.code).resolve()))

    def test_merge_requires_an_explicit_repo(self):
        # An inferred repo would be the PM directory's own remote (PR #35 review finding 1).
        self.fix_with_test()
        result = self.gate("merge", "--task", "T020", "--dir", self.code, "--base", "main", "--pr", "45")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--repo", result.stderr)

    # --- the shared ledger ------------------------------------------------------------------

    def test_merge_evidence_never_counts_as_a_review_round(self):
        self.fix_with_test()
        self.full_evidence()
        self.gate("override", "--task", "T020", "--dir", self.code, "--check", "observation", "--reason", "r")
        self.check()
        self.assertEqual(review_gate.rounds_used(self.pm / "logs", "reviewer", "T020"), 0)
        self.assertEqual(review_gate.rounds_used(self.pm / "logs", "critic", "T020"), 0)
        self.assertEqual(review_gate.critique_state(self.pm / "logs", "T020"), (None, None))

    def test_plan_fields_parse_quoted_commands_with_hashes(self):
        task = review_gate.plan_task(self.pm / "PLAN.md", "T020")
        self.assertEqual(task["observation"], "test")
        self.assertEqual(task["observation_cmd"], "bash tests/check.sh   # the named regression test")
        self.assertEqual(review_gate.plan_task(self.pm / "PLAN.md", "T023").get("observation"), None)


    # --- issue #58 item 3: the security floor at merge ---------------------------------------

    def security_change(self):
        self.write("src/app.txt", "hardened\n")
        return self.commit("tighten input validation")

    def test_security_task_needs_an_opus_adjudication_pinned_to_the_commit(self):
        self.security_change()
        self.full_evidence("T026")
        result = self.check("T026")
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertEqual(self.checks(result)["security"], "fail")
        self.assertIn("no merge-time adjudication", result.stderr)
        refused = self.gate("adjudicate", "--task", "T026", "--dir", self.code, "--model", "sonnet")
        self.assertEqual(refused.returncode, 31)
        self.assertIn("Opus-class", refused.stderr)
        for model in ("fable", "openrouter/anthropic/claude-opus-4.7"):
            self.assertEqual(self.gate("adjudicate", "--task", "T026", "--dir", self.code,
                                       "--model", model).returncode, 31, model)
        ok = self.gate("adjudicate", "--task", "T026", "--dir", self.code, "--model", "claude-opus-4-7")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["event"], "merge_adjudication")
        passed = self.check("T026")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(self.checks(passed)["security"], "pass")
        # A new commit voids it, like every other piece of merge evidence.
        self.write("src/app.txt", "hardened more\n")
        self.commit("follow-up")
        self.full_evidence("T026")
        self.assertEqual(self.checks(self.check("T026"))["security"], "fail")

    def test_security_check_rejects_a_non_opus_row_the_cli_would_have_refused(self):
        # The gate must not trust the recording path: a hand-appended row from another model
        # (or one recorded before the task was flagged security) still fails the check.
        sha = self.security_change()
        self.full_evidence("T026")
        review_gate.append_row(self.pm / "logs", {"event": "merge_adjudication", "task_id": "T026",
                                                   "sha": sha, "outcome": "approve", "model": "sonnet"})
        result = self.check("T026")
        self.assertEqual(self.checks(result)["security"], "fail")
        self.assertIn("recorded by sonnet, not an Opus-class model", result.stderr)
        self.gate("override", "--task", "T026", "--dir", self.code, "--check", "security",
                  "--reason", "operator: Opus unavailable, accepted the risk")
        self.assertEqual(self.checks(self.check("T026"))["security"], "overridden")

    def test_adjudication_needs_an_approving_review_of_the_commit(self):
        self.security_change()
        self.gate("verify", "--task", "T026", "--dir", self.code)
        no_review = self.gate("adjudicate", "--task", "T026", "--dir", self.code, "--model", "opus")
        self.assertEqual(no_review.returncode, 31)
        self.assertIn("no approving review", no_review.stderr)
        self.review("T026", verdict="REJECT", blockers=1)
        self.assertEqual(self.gate("adjudicate", "--task", "T026", "--dir", self.code,
                                   "--model", "opus").returncode, 31)

    def test_a_non_security_task_skips_the_floor_and_any_model_may_adjudicate(self):
        self.fix_with_test()
        self.full_evidence()
        recorded = self.gate("adjudicate", "--task", "T020", "--dir", self.code, "--model", "sonnet")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(self.checks(self.check())["security"], "skipped")

    # --- issue #58 item 4: test support paths are overlaid onto the base ---------------------

    def pbxproj_scenario(self):
        """A runner that only runs the tests the project file registers (the Xcode shape)."""
        self.git("checkout", "-q", "main")
        self.write("tests/run.sh", 'for t in $(cat app.pbxproj); do bash "tests/$t" || exit 1; done\n')
        self.write("app.pbxproj", "")
        self.commit("test runner driven by the project file")
        self.git("checkout", "-q", "task/T020")
        self.git("rebase", "-q", "main")
        self.write("src/app.txt", "fixed\n")
        self.write("tests/new_check.sh", REAL_CHECK)
        self.write("app.pbxproj", "new_check.sh\n")
        return self.commit("fix + a new test registered in the project file")

    def test_test_support_paths_are_overlaid_onto_the_base_tree(self):
        self.pbxproj_scenario()
        args = ("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main",
                "--cmd", "bash tests/run.sh")
        # Without the policy the project file stays at base: the new test never runs there, the
        # base run passes, and the gate reports an inert observation.
        before = self.gate(*args)
        self.assertEqual(before.returncode, 1, before.stderr)
        self.assertIn("PASSES on the base tree", before.stderr)
        with open(self.pm / "PROJECT.md", "a") as handle:
            handle.write("\n## Test support paths\n\n- *.pbxproj\n")
        after = self.gate(*args)
        self.assertEqual(after.returncode, 0, after.stderr)
        row = json.loads(after.stdout)
        self.assertEqual(row["support_overlaid"], ["app.pbxproj"])
        self.assertIn("app.pbxproj", row["overlaid"])
        # Still a production path: the production-diff check is unchanged by the declaration.
        self.full_evidence(cmd="true")
        self.assertIn("app.pbxproj", json.loads(self.check().stdout)["checks"]["production_diff"]["detail"])

    def test_a_test_support_change_alone_is_not_a_changed_test(self):
        with open(self.pm / "PROJECT.md", "a") as handle:
            handle.write("\n## Test support paths\n\n- *.pbxproj\n")
        self.write("src/app.txt", "fixed\n")
        self.write("app.pbxproj", "wiring only\n")
        self.commit("fix + project file, no test")
        fob = self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        self.assertEqual(fob.returncode, 31)
        self.assertIn("changes no test path", fob.stderr)

    # --- issue #58 item 5: a stale base is refreshed or refused ------------------------------

    def with_remote(self):
        """origin = a bare clone of main; local main tracks it; another clone then pushes."""
        remote = Path(self.temp.name) / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", "-b", "main", str(self.code), str(remote)],
                       check=True, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("fetch", "-q", "origin")
        self.git("branch", "-q", "--set-upstream-to", "origin/main", "main")
        other = Path(self.temp.name) / "other"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True,
                       capture_output=True)
        (other / "upstream.txt").write_text("landed after our last fetch\n")
        for cmd in (["add", "-A"], ["commit", "-q", "-m", "upstream work"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", str(other), "-c", "user.email=t@t", "-c", "user.name=t", *cmd],
                           check=True, capture_output=True)
        return remote, subprocess.run(["git", "-C", str(other), "rev-parse", "HEAD"], check=True,
                                      capture_output=True, text=True).stdout.strip()

    def test_a_remote_tracking_base_is_fetched_before_use(self):
        self.fix_with_test()
        _, pushed = self.with_remote()
        self.assertNotEqual(self.git("rev-parse", "origin/main"), pushed)
        result = self.gate("check", "--task", "T020", "--dir", self.code, "--base", "origin/main")
        report = json.loads(result.stdout)
        self.assertTrue(report["base_fetched"])
        self.assertEqual(self.git("rev-parse", "origin/main"), pushed, "the gate must refresh the base")
        self.assertTrue(self.ledger()[-1]["base_fetched"])

    def test_no_fetch_uses_the_ref_as_is_and_says_so(self):
        self.fix_with_test()
        _, pushed = self.with_remote()
        stale = self.git("rev-parse", "origin/main")
        result = self.gate("check", "--task", "T020", "--dir", self.code, "--base", "origin/main",
                           "--no-fetch")
        self.assertEqual(self.git("rev-parse", "origin/main"), stale)
        self.assertIn("--no-fetch", result.stderr)
        self.assertFalse(json.loads(result.stdout)["base_fetched"])
        self.assertNotEqual(stale, pushed)

    def test_a_local_base_behind_its_upstream_is_refused(self):
        self.fix_with_test()
        self.with_remote()
        for command in (("check",), ("fail-on-base",)):
            result = self.gate(*command, "--task", "T020", "--dir", self.code, "--base", "main")
            self.assertEqual(result.returncode, 31, result.stdout + result.stderr)
            self.assertIn("1 commit(s) behind its upstream origin/main", result.stderr)
        # Fast-forwarded, the same local base is accepted.
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--ff-only", "origin/main")
        self.git("checkout", "-q", "task/T020")
        result = self.gate("check", "--task", "T020", "--dir", self.code, "--base", "main")
        self.assertNotIn("behind its upstream", result.stderr)

    def test_an_unreachable_remote_is_a_policy_stop_not_a_silent_stale_base(self):
        self.fix_with_test()
        remote, _ = self.with_remote()
        self.git("remote", "set-url", "origin", str(remote) + "-gone")
        result = self.gate("check", "--task", "T020", "--dir", self.code, "--base", "origin/main")
        self.assertEqual(result.returncode, 31)
        self.assertIn("could not fetch main from origin", result.stderr)

    def test_a_local_base_with_no_upstream_warns(self):
        self.fix_with_test()
        result = self.check()
        self.assertIn("no upstream", result.stderr)
        self.assertEqual(json.loads(result.stdout)["base_warnings"][0][:12], "--base main ")

    # --- issue #58 item 1: closing a merge-gated task needs the merge gate -------------------

    def managed(self):
        from pm_state import PMState
        (self.pm / ".orchestrator").mkdir(exist_ok=True)
        (self.pm / ".orchestrator/config.json").write_text('{"version":1}')
        (self.pm / "TASK_LOG.md").write_text("# Task log\n")
        state = PMState(self.pm)
        token = state.acquire("codex", "session", os.getpid())["token"]
        return state, token, dict(os.environ, PM_ORCHESTRATOR_TOKEN=token)

    def close(self, state, token, task, operation):
        import hashlib
        current = (self.pm / "PLAN.md").read_text()
        chunks = current.split(f"  - id: {task}\n")
        candidate = chunks[0] + f"  - id: {task}\n" + chunks[1].replace("status: in_progress", "status: done", 1)
        return state.update_plan(token, hashlib.sha256(current.encode()).hexdigest(), candidate,
                                 f"### task_completed\ntask_id: {task}\n", operation)

    def test_a_gated_task_cannot_be_closed_before_the_merge_gate_passes(self):
        from pm_state import StateError
        state, token, env = self.managed()
        self.fix_with_test()
        with self.assertRaises(StateError) as refused:
            self.close(state, token, "T020", "close-1")
        self.assertIn("T020 cannot be marked done: no merge_gate.py check", str(refused.exception))
        self.full_evidence()
        self.gate("fail-on-base", "--task", "T020", "--dir", self.code, "--base", "main")
        self.check()
        self.assertTrue(self.ledger()[-1]["allowed"])
        self.close(state, token, "T020", "close-2")
        self.assertIn("status: done", (self.pm / "PLAN.md").read_text().split("- id: T021")[0])

    def test_a_refused_check_or_failed_merge_does_not_close_the_task(self):
        state, token, env = self.managed()
        self.fix_with_test()
        self.full_evidence()
        self.check()                                  # observation missing: refused
        self.assertFalse(self.ledger()[-1]["allowed"])
        from pm_state import StateError
        with self.assertRaises(StateError) as refused:
            self.close(state, token, "T020", "close-1")
        self.assertIn("refused the merge", str(refused.exception))
        review_gate.append_row(self.pm / "logs", {"event": "merge_check", "task_id": "T020",
                                                   "sha": "a" * 40, "allowed": True})
        review_gate.append_row(self.pm / "logs", {"event": "merge", "task_id": "T020",
                                                   "sha": "a" * 40, "exit": 1})
        self.assertEqual(merge_gate.close_evidence(self.pm / "logs", "T020")[0], False)
        review_gate.append_row(self.pm / "logs", {"event": "merge", "task_id": "T020",
                                                   "sha": "a" * 40, "exit": 0})
        self.assertEqual(merge_gate.close_evidence(self.pm / "logs", "T020")[0], True)

    def test_an_operator_exemption_closes_a_gate2_skip(self):
        from pm_state import StateError
        state, token, env = self.managed()
        no_reason = self.gate("exempt-close", "--task", "T021", "--kind", "gate2-skip", env=env)
        self.assertEqual(no_reason.returncode, 31)
        with self.assertRaises(StateError):
            self.close(state, token, "T021", "close-1")
        stranger = dict(env, PM_ORCHESTRATOR_TOKEN="not-the-lease")
        self.assertEqual(self.gate("exempt-close", "--task", "T021", "--kind", "gate2-skip",
                                   "--reason", "x", env=stranger).returncode, 31)
        recorded = self.gate("exempt-close", "--task", "T021", "--kind", "gate2-skip",
                             "--reason", "operator chose skip at Gate 2 (T021 twice exit 1)", env=env)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.close(state, token, "T021", "close-2")
        status = json.loads(self.gate("status", "--task", "T021").stdout)
        self.assertEqual(status["close"]["allowed"], True)
        self.assertIn("gate2-skip", status["close"]["detail"])

    def test_legacy_design_and_already_done_tasks_close_without_the_gate(self):
        state, token, _ = self.managed()
        self.close(state, token, "T023", "close-legacy")      # no observation: field
        self.close(state, token, "T027", "close-designer")    # agent: designer
        self.assertEqual(merge_gate.close_gated({"agent": "user", "observation": "none"})[0], False)
        self.assertEqual(merge_gate.close_gated({"agent": "pm", "observation": "runtime"})[0], True)
        self.assertEqual(merge_gate.close_gated({"agent": "codex gpt-5.6-terra",
                                                 "observation": "test"})[0], True)
        # A task that was already done is not re-judged when another field of the PLAN changes.
        done = PLAN.replace("status: in_progress", "status: done")
        self.assertEqual(merge_gate.close_problems(self.pm, done, done.replace("R0", "R1")), [])
        # Gated: every task carrying the field (T020-T022, T024-T026, incl. a null one); not
        # T023 (legacy) or T027 (designer).
        self.assertEqual([p.split()[0] for p in merge_gate.close_problems(self.pm, PLAN, done)],
                         ["T020", "T021", "T022", "T024", "T025", "T026"])

    def test_plan_update_cli_refuses_the_close_with_exit_31(self):
        import hashlib
        _, token, env = self.managed()
        current = (self.pm / "PLAN.md").read_text()
        candidate = self.pm / "candidate.md"
        head, tail = current.split("  - id: T020\n")
        candidate.write_text(head + "  - id: T020\n" + tail.replace("status: in_progress", "status: done", 1))
        result = subprocess.run([sys.executable, SCRIPTS / "plan-update.py", "--pm-dir", self.pm,
                                 "--token", token, "--expected-hash",
                                 hashlib.sha256(current.encode()).hexdigest(),
                                 "--candidate", candidate, "--event", "### task_completed\n",
                                 "--operation-id", "cli-close"], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 31, result.stdout + result.stderr)
        self.assertIn("T020 cannot be marked done", result.stderr)
        self.assertEqual((self.pm / "PLAN.md").read_text(), current)

if __name__ == "__main__":
    unittest.main()
