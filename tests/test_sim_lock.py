import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sim-lock.py"


class SimLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "VIBETASTIC_SIM_LOCK": str(Path(self.tmp.name) / "sim.lock")}
        self.env.pop("VIBETASTIC_SIM_LOCK_HELD", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_lock(self, *args, **kwargs):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True, **kwargs)

    def test_passes_through_command_status(self):
        self.assertEqual(self.run_lock("--", "sh", "-c", "exit 7").returncode, 7)
        self.assertEqual(self.run_lock("--", "true").returncode, 0)

    def test_second_holder_waits_then_times_out(self):
        holder = subprocess.Popen([sys.executable, str(SCRIPT), "--", "sleep", "3"], env=self.env)
        try:
            time.sleep(0.5)
            result = self.run_lock("--timeout", "0.5", "--", "true")
            self.assertEqual(result.returncode, 75)
            self.assertIn("timed out", result.stderr)
        finally:
            holder.wait()
        self.assertEqual(self.run_lock("--timeout", "1", "--", "true").returncode, 0)

    def test_runs_serialize(self):
        log = Path(self.tmp.name) / "order.log"
        step = f"echo start >> {log}; sleep 0.6; echo end >> {log}"
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--", "sh", "-c", step], env=self.env) for _ in range(2)]
        for proc in procs:
            self.assertEqual(proc.wait(), 0)
        self.assertEqual(log.read_text().split(), ["start", "end", "start", "end"])

    def test_nested_call_does_not_deadlock(self):
        inner = f"{sys.executable} {SCRIPT} --timeout 1 -- true"
        self.assertEqual(self.run_lock("--timeout", "1", "--", "sh", "-c", inner).returncode, 0)


if __name__ == "__main__":
    unittest.main()
