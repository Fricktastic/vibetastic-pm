#!/usr/bin/env python3
"""`<file>.md § <Section>` references must resolve (RULES.md § Checks must be able to fail)."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check-refs.py"


class CheckRefsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".claude/rules").mkdir(parents=True)
        (self.root / "VERIFY.md").write_text(textwrap.dedent("""\
            # Verify
            ## Who runs what
            ## Merge gate — pin the tree (issue #35)
            ```
            ## A heading inside a code sample
            ```
            """))
        (self.root / ".claude/rules/dispatch.md").write_text(
            "## Round caps\n- **codex + iOS/Xcode tasks** (`X`): details\n")

    def tearDown(self):
        self.temp.cleanup()

    def run_on(self, text):
        (self.root / "RULES.md").write_text(text)
        return subprocess.run([sys.executable, CHECK, "--root", self.root], capture_output=True, text=True)

    def test_resolving_references_pass(self):
        result = self.run_on(textwrap.dedent("""\
            See `VERIFY.md` § Who runs what (issue #36), framework/VERIFY.md § Merge gate: the
            four checks, `.claude/rules/dispatch.md` § Round caps, dispatch.md § codex + iOS/Xcode
            tasks, and a wrapped one: `framework/VERIFY.md` §
            Merge gate. Project files are the deployment's: `PROJECT.md § Anything at all`.
            """))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("5 section reference(s), 0 dangling", result.stdout)

    def test_renamed_heading_is_dangling(self):
        result = self.run_on("See VERIFY.md § Who verifies what.\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn('RULES.md:1: VERIFY.md has no heading matching "§ Who verifies what"', result.stderr)

    def test_missing_file_and_code_sample_headings_do_not_count(self):
        for text in ("See NOPE.md § Anything.\n", "See VERIFY.md § A heading inside a code sample.\n"):
            self.assertEqual(self.run_on(text).returncode, 1, text)

    def test_the_framework_itself_has_no_dangling_reference(self):
        result = subprocess.run([sys.executable, CHECK], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
