"""Terminal entrypoint. Streams the agent's work, then writes the memo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, memo
from .agent import AuditAgent
from .models import AuditResult

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
    p.add_argument("--mock", action="store_true",
                   help="force the mock detector (development only)")
    p.add_argument("--allow-mock", action="store_true",
                   help="permit memo output from a mock run (numbers are fake)")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="also write audit.json")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    colour = _supports_colour()

    def c(code, text):
        return "%s%s%s" % (code, text, RESET) if colour else text

    catalog = Path(args.catalog)
    if not catalog.is_dir():
        print("Not a folder: %s" % catalog, file=sys.stderr)
        return 2

    agent = AuditAgent(force_mock=args.mock)

    print()
    for ev in agent.run(
        catalog,
        royalties_csv=args.royalties,
        truth_csv=args.truth,
        multiple=args.multiple,
        asking_price=args.asking_price,
        catalog_name=args.name,
    ):
        if ev.type == "plan":
            print(c(BOLD, "Audit plan"))
            for i, step in enumerate(ev.data["steps"], 1):
                print(c(DIM, "  %d. %s" % (i, step)))
            print()
        elif ev.type == "step":
            print("\n%s" % c(BOLD, "> " + ev.title))
            if ev.detail:
                print(c(DIM, "  " + ev.detail))
        elif ev.type == "tool_call":
            print(c(CYAN, "  -> %s" % ev.title), c(DIM, ev.detail))
        elif ev.type == "tool_result":
            print("    %s" % ev.detail)
        elif ev.type == "finding":
            print("\n%s" % c(YELLOW + BOLD, "  * " + ev.title))
            print("    %s" % ev.detail)
        elif ev.type == "verdict":
            print("\n%s" % c(BOLD, "  " + ev.title))
            print("    %s" % ev.detail)
        elif ev.type == "warn":
            print("\n%s" % c(YELLOW, "  ! %s" % ev.title))
            print(c(YELLOW, "    %s" % ev.detail))
        elif ev.type == "error":
            print(c(RED, "\n  x %s: %s" % (ev.title, ev.detail)))
            return 1
        elif ev.type == "done":
            print("\n%s" % c(GREEN, "  " + ev.title), c(DIM, ev.detail))

    result = agent.result
    if result is None:
        return 1

    try:
        path = memo.write(result, allow_mock=args.allow_mock)
        print(c(DIM, "\n  memo: %s" % path))
    except memo.MockModeRefused as exc:
        print(c(YELLOW, "\n  memo not written: %s" % exc))

    if args.json_out:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        jpath = config.OUTPUT_DIR / "audit.json"
        jpath.write_text(json.dumps(result.to_dict(), indent=2, default=str),
                         encoding="utf-8")
        print(c(DIM, "  json: %s" % jpath))

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
