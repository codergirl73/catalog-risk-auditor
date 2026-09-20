"""The terminal entrypoint.

The CLI is what a judge watches and what an analyst runs, and until these
tests existed it was the only module in the package with no coverage at all.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from catalog_audit import config
from catalog_audit.cli import _Printer, build_parser, main
from catalog_audit.models import Event


class TestParser(unittest.TestCase):
    def test_catalog_is_required(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_budget_is_an_integer(self):
        args = build_parser().parse_args(["cat", "--budget", "25"])
        self.assertEqual(args.budget, 25)

    def test_defaults_are_conservative(self):
        args = build_parser().parse_args(["cat"])
        self.assertFalse(args.mock)
        self.assertFalse(args.allow_mock)
        self.assertFalse(args.json_out)
        self.assertIsNone(args.budget)


class TestPrinter(unittest.TestCase):
    """One method per event type; an unknown type must not crash the run."""

    def render(self, event, colour=False):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _Printer(colour).show(event)
        return buf.getvalue()

    def test_every_event_type_renders(self):
        cases = [
            Event(type="plan", title="Audit plan", data={"steps": ["a", "b"]}),
            Event(type="step", title="Score", detail="detector: x"),
            Event(type="tool_call", title="call", detail="d"),
            Event(type="tool_result", title="r", detail="12 scored"),
            Event(type="finding", title="Tier breakdown", detail="clean 1"),
            Event(type="verdict", title="Exposure", detail="$1"),
            Event(type="warn", title="Careful", detail="something"),
            Event(type="error", title="Empty", detail="no files"),
            Event(type="done", title="Complete", detail="manifest abc"),
        ]
        for event in cases:
            with self.subTest(event=event.type):
                self.assertTrue(self.render(event).strip())

    def test_plan_numbers_its_steps(self):
        out = self.render(Event(type="plan", data={"steps": ["first", "second"]}))
        self.assertIn("1. first", out)
        self.assertIn("2. second", out)

    def test_unknown_event_type_is_ignored_not_fatal(self):
        self.assertEqual(self.render(Event(type="telemetry", title="x")), "")

    def test_colour_adds_escapes_and_plain_does_not(self):
        event = Event(type="finding", title="T", detail="D")
        self.assertIn("\033[", self.render(event, colour=True))
        self.assertNotIn("\033[", self.render(event, colour=False))


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.catalog = root / "catalog"
        (self.catalog / "human").mkdir(parents=True)
        for i in range(3):
            (self.catalog / "human" / ("t%d.mp3" % i)).write_bytes(b"audio-%d" % i)

        self.royalties = root / "r.csv"
        self.royalties.write_text(
            "filename,title,artist,annual_usd\n"
            + "".join("t%d.mp3,T,A,%d\n" % (i, 100 * (i + 1)) for i in range(3)),
            encoding="utf-8")

        self._out = config.OUTPUT_DIR
        config.OUTPUT_DIR = root / "out"

    def tearDown(self):
        config.OUTPUT_DIR = self._out
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def test_missing_catalog_folder_exits_two(self):
        code, _ = self.run_cli("no/such/folder")
        self.assertEqual(code, 2)

    def test_empty_catalog_exits_one(self):
        with tempfile.TemporaryDirectory() as empty:
            code, out = self.run_cli(empty, "--mock")
            self.assertEqual(code, 1)
            self.assertIn("Empty catalog", out)

    def test_a_mock_run_succeeds_but_refuses_the_memo(self):
        code, out = self.run_cli(str(self.catalog), "--mock",
                                 "--royalties", str(self.royalties))
        self.assertEqual(code, 0)
        self.assertIn("memo not written", out)
        self.assertFalse((config.OUTPUT_DIR / "risk_memo.html").exists())

    def test_allow_mock_writes_a_stamped_memo(self):
        code, _ = self.run_cli(str(self.catalog), "--mock", "--allow-mock")
        self.assertEqual(code, 0)
        memo = config.OUTPUT_DIR / "risk_memo.html"
        self.assertTrue(memo.exists())
        self.assertIn("MOCK DATA", memo.read_text(encoding="utf-8"))

    def test_json_flag_writes_a_machine_readable_record(self):
        code, _ = self.run_cli(str(self.catalog), "--mock", "--allow-mock",
                               "--json", "--royalties", str(self.royalties))
        self.assertEqual(code, 0)
        payload = json.loads(
            (config.OUTPUT_DIR / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["assets"]), 3)
        self.assertIn("valuation", payload)

    def test_budget_flag_limits_live_calls(self):
        code, out = self.run_cli(str(self.catalog), "--mock", "--budget", "1")
        self.assertEqual(code, 0)
        self.assertIn("1 live calls spent of 1 budgeted", out)

    def test_the_plan_is_printed_before_any_result(self):
        _, out = self.run_cli(str(self.catalog), "--mock")
        self.assertLess(out.index("Audit plan"), out.index("Tier breakdown"))


if __name__ == "__main__":
    unittest.main()
