import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class SetupTests(unittest.TestCase):
    def test_existing_project_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            pm=Path(d)
            original='---\nbuilder_backends: [claude]\n---\ncustom state'
            (pm/'PROJECT.md').write_text(original)
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual((pm/'PROJECT.md').read_text(),original)
            self.assertFalse((pm/'.claude').exists())

    def test_fresh_project_installs_both_harnesses(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertTrue((Path(d)/'.codex/hooks.json').is_file())
            self.assertTrue((Path(d)/'AGENTS.md').is_file())
