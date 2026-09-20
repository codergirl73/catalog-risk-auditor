"""Terminal entrypoint. Streams the agent's work, then writes the memo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, memo, webui
from .agent import AuditAgent

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _supports_colour() -> bool:
    return sys.stdout.isatty()


def build_parser() -> argparse.ArgumentParser:
    """Command-line surface. Every flag maps to one audit decision."""
    p = argparse.ArgumentParser(
        prog="catalog-audit",
        description="Audit a music catalog for AI-generated content before "
                    "acquisition.",
    )
    p.add_argument("catalog", help="folder of audio files to audit")
    p.add_argument("--royalties", help="CSV: filename,title,artist,annual_usd")
    p.add_argument("--truth", help="CSV: filename,true_label (ai|human)")
    p.add_argument("--multiple", type=float, default=None,
                   help="acquisition multiple (default %.1f)"
                        % config.DEFAULT_MULTIPLE)
    p.add_argument("--asking-price", type=float, default=None,
                   help="asking price in USD; overrides the multiple")
    p.add_argument("--name", default=None, help="catalog name for the memo")
    p.add_argument("--budget", type=int, default=None,
                   help="max live API calls for this run (default %d)"
                        % config.HS_CREDIT_BUDGET)
    p.add_argument("--mock", action="store_true",
                   help="force the mock detector (development only)")
    p.add_argument("--allow-mock", action="store_true",
                   help="permit memo output from a mock run (numbers are fake)")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="also write audit.json")
    p.add_argument("--serve", action="store_true",
                   help="watch the audit in a browser instead of the terminal")
    p.add_argument("--port", type=int, default=None,
                   help="port for --serve (default %d)" % webui.DEFAULT_PORT)
    return p


class _Printer:
    """Renders Events to a terminal.

    One method per event type rather than one long chain of comparisons, so
    adding an event type means adding a method and nothing else.
    """

    def __init__(self, colour: bool) -> None:
        self.colour = colour

    def paint(self, code: str, text: str) -> str:
        return "%s%s%s" % (code, text, RESET) if self.colour else text

    def plan(self, ev) -> None:
        print(self.paint(BOLD, "Audit plan"))
        for i, step in enumerate(ev.data["steps"], 1):
            print(self.paint(DIM, "  %d. %s" % (i, step)))
        print()

    def step(self, ev) -> None:
        print("\n%s" % self.paint(BOLD, "> " + ev.title))
        if ev.detail:
            print(self.paint(DIM, "  " + ev.detail))

    def tool_call(self, ev) -> None:
        print(self.paint(CYAN, "  -> %s" % ev.title),
              self.paint(DIM, ev.detail))

    def tool_result(self, ev) -> None:
        print("    %s" % ev.detail)

    def finding(self, ev) -> None:
        print("\n%s" % self.paint(YELLOW + BOLD, "  * " + ev.title))
        print("    %s" % ev.detail)

    def verdict(self, ev) -> None:
        print("\n%s" % self.paint(BOLD, "  " + ev.title))
        print("    %s" % ev.detail)

    def warn(self, ev) -> None:
        print("\n%s" % self.paint(YELLOW, "  ! %s" % ev.title))
        print(self.paint(YELLOW, "    %s" % ev.detail))

    def error(self, ev) -> None:
        print(self.paint(RED, "\n  x %s: %s" % (ev.title, ev.detail)))

    def done(self, ev) -> None:
        print("\n%s" % self.paint(GREEN, "  " + ev.title),
              self.paint(DIM, ev.detail))

    def show(self, ev) -> None:
        handler = getattr(self, ev.type, None)
        if handler is not None:
            handler(ev)


def _write_outputs(result, out: _Printer, allow_mock: bool,
                   json_out: bool) -> None:
    """The memo, and optionally the machine-readable record beside it."""
    try:
        path = memo.write(result, allow_mock=allow_mock)
        print(out.paint(DIM, "\n  memo: %s" % path))
    except memo.MockModeRefusedError as exc:
        print(out.paint(YELLOW, "\n  memo not written: %s" % exc))

    if json_out:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = config.OUTPUT_DIR / "audit.json"
        json_path.write_text(
            json.dumps(result.to_dict(), indent=2, default=str),
            encoding="utf-8")
        print(out.paint(DIM, "  json: %s" % json_path))


def main(argv=None) -> int:
    """Run an audit and write its outputs.

    Returns 0 on success, 1 if the audit could not complete, and 2 if the
    catalog path is not a folder.
    """
    args = build_parser().parse_args(argv)
    out = _Printer(_supports_colour())

    catalog = Path(args.catalog)
    if not catalog.is_dir():
        print("Not a folder: %s" % catalog, file=sys.stderr)
        return 2

    if args.serve:
        webui.serve(webui.options_from(args),
                    port=args.port or webui.DEFAULT_PORT)
        return 0

    agent = AuditAgent(force_mock=args.mock, budget_limit=args.budget)

    print()
    for ev in agent.run(
        catalog,
        royalties_csv=args.royalties,
        truth_csv=args.truth,
        multiple=args.multiple,
        asking_price=args.asking_price,
        catalog_name=args.name,
    ):
        out.show(ev)
        if ev.type == "error":
            return 1

    if agent.result is None:
        return 1

    _write_outputs(agent.result, out, args.allow_mock, args.json_out)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
