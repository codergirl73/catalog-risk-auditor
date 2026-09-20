"""The operational scripts are not imported by any other test.

That gap let a broken import ship once: an edit removed a name the module
still used, every unit test passed, and the failure would only have appeared
when somebody ran the script by hand on the day. These tests execute each
entrypoint so a missing import cannot hide.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(p.name for p in (ROOT / "scripts").glob("*.py"))


class TestScriptsAreRunnable(unittest.TestCase):
    def test_there_are_scripts_to_check(self):
        self.assertTrue(SCRIPTS, "no scripts found to smoke-test")

    def test_every_script_runs_its_help(self):
        """--help exercises imports and argument parsing without side effects."""
        for name in SCRIPTS:
            with self.subTest(script=name):
                result = subprocess.run(  # noqa: S603
                    [sys.executable, str(ROOT / "scripts" / name), "--help"],
                    capture_output=True, text=True, timeout=60, check=False,
                )
                self.assertEqual(
                    result.returncode, 0,
                    "%s --help failed:\n%s" % (name, result.stderr[:800]))

    def test_every_script_compiles(self):
        import py_compile
        for name in SCRIPTS:
            with self.subTest(script=name):
                py_compile.compile(str(ROOT / "scripts" / name),
                                   doraise=True, cfile=None)

    def test_entrypoint_runs_its_help(self):
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "run.py"), "--help"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[:800])


if __name__ == "__main__":
    unittest.main()
