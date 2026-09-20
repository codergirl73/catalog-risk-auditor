#!/usr/bin/env python3
"""Build a static, shareable replay of a real audit.

The live UI needs Python running, so it cannot be a link somebody clicks from
a submission page. This records one real run and renders it as a static page
that replays the same events with the same visuals, next to the memo it
produced.

Nothing is recreated or dramatised: the events are the ones the agent emitted
and the numbers are the ones it computed. The run is replayed from the
response cache, so building the site spends no credits.

    python3 scripts/build_demo_site.py \\
        --catalog data/demo_catalog \\
        --royalties data/royalties.csv --truth data/ground_truth.csv

Writes docs/index.html and docs/memo.html, which GitHub Pages serves.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from catalog_audit import memo, webui  # noqa: E402
from catalog_audit.agent import AuditAgent  # noqa: E402

# Pace the replay so a viewer can follow it without waiting out the real
# analysis time. Fast enough to hold attention, slow enough to read.
STEP_PAUSE_MS = 520
EVENT_PAUSE_MS = 190


def record(options: dict) -> tuple:
    """Run the audit and return (events, result)."""
    agent = AuditAgent(force_mock=options["mock"],
                       budget_limit=options["budget"])
    events = []
    for event in agent.run(
        options["catalog"],
        royalties_csv=options["royalties"],
        truth_csv=options["truth"],
        multiple=options["multiple"],
        asking_price=options["asking_price"],
        catalog_name=options["name"],
    ):
        events.append({
            "type": event.type,
            "title": event.title,
            "detail": event.detail,
            "data": {k: v for k, v in event.data.items()
                     if isinstance(v, (str, int, float, bool, list, dict,
                                       type(None)))},
        })
    return events, agent.result


BANNER = """
<div class="banner">
  <strong>This is a recording of a real run.</strong>
  Every event below was emitted by the agent and every number was computed by
  it, against live HumanStandard API responses. Press play to watch it again,
  or <a href="memo.html">read the memo it produced</a>.
</div>
"""

EXTRA_CSS = """
.banner{margin:18px 32px 0;padding:14px 18px;border:1px solid var(--line);
  border-left:3px solid var(--contested);border-radius:8px;
  background:var(--panel);color:var(--ink2);font-size:13.5px}
.banner strong{color:var(--ink)}
.banner a{color:var(--clean)}
footer{padding:26px 32px 46px;color:var(--muted);font-size:12.5px;
  border-top:1px solid var(--line);margin-top:30px}
footer a{color:var(--clean)}
"""

REPLAY_JS = """
'use strict';
const EVENTS = __EVENTS__;
const SUMMARY = __SUMMARY__;
const STEP_PAUSE = __STEP_PAUSE__;
const EVENT_PAUSE = __EVENT_PAUSE__;

function play() {
  const button = $('run');
  button.disabled = true;
  button.textContent = 'Replaying\\u2026';
  $('log').innerHTML = '';
  $('result').classList.add('hidden');
  $('accuracy').classList.add('hidden');
  $('queue-wrap').classList.add('hidden');

  let i = 0;
  const tick = () => {
    if (i >= EVENTS.length) {
      showSummary(SUMMARY);
      button.disabled = false;
      button.textContent = 'Replay';
      return;
    }
    const ev = EVENTS[i++];
    if (ev.type === 'plan') {
      steps = (ev.data && ev.data.steps) || [];
      renderPlan();
      $('budget').classList.remove('hidden');
    }
    if (ev.type === 'step') markStep(ev.title);
    if (ev.title === 'budget check') $('budget-v').textContent = ev.detail;
    if (ev.title === 'runtime estimate') {
      $('runtime-v').textContent = ev.detail.split(' at ')[0];
    }
    if (ev.title === 'Accuracy against ground truth') {
      $('accuracy').classList.remove('hidden');
      $('accuracy-text').textContent = ev.detail;
    }
    if (ev.type === 'done') finishPlan();
    log(ev.type, ev.title, ev.detail);
    setTimeout(tick, ev.type === 'step' ? STEP_PAUSE : EVENT_PAUSE);
  };
  tick();
}

$('run').addEventListener('click', play);
setTimeout(play, 400);
"""


def build(events: list, result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # The replay reuses the live UI's markup and stylesheet, so the recording
    # and the running tool look like the same thing, because they are.
    page = webui.PAGE
    page = page.replace('<link rel="stylesheet" href="/app.css">',
                        "<style>%s%s</style>" % (webui.STYLE, EXTRA_CSS))
    page = page.replace('<script src="/app.js"></script>', "")
    page = page.replace("<main>", BANNER + "<main>")
    page = page.replace('<a id="memo" href="/memo" target="_blank"',
                        '<a id="memo" href="memo.html" target="_blank"')
    page = page.replace("<title>Catalog Risk Auditor</title>",
                        "<title>Catalog Risk Auditor &mdash; recorded run</title>")
    page = page.replace("<button id=\"run\">Run audit</button>",
                        "<button id=\"run\">Replay</button>")

    shared = webui.SCRIPT.split("$('run').addEventListener")[0]
    replay = (REPLAY_JS
              .replace("__EVENTS__", json.dumps(events))
              .replace("__SUMMARY__", json.dumps(webui._summary(result)))
              .replace("__STEP_PAUSE__", str(STEP_PAUSE_MS))
              .replace("__EVENT_PAUSE__", str(EVENT_PAUSE_MS)))

    footer = (
        '<footer>Recorded %s &middot; detector %s &middot; evidence manifest '
        '%s<br>Source: <a href="https://github.com/codergirl73/'
        'catalog-risk-auditor">github.com/codergirl73/catalog-risk-auditor</a>'
        ' &middot; the catalog and its royalty figures are constructed for '
        'demonstration; the detection results are real.</footer>'
        % (html.escape(str(result.catalog_name)), html.escape(result.provider),
           html.escape(result.manifest_sha256[:16])))

    page = page.replace("</body>", "%s<script>%s\n%s</script></body>"
                        % (footer, shared, replay))
    (out_dir / "index.html").write_text(page, encoding="utf-8")

    (out_dir / "memo.html").write_text(
        memo.render(result), encoding="utf-8")

    # Tell Pages not to run the content through Jekyll.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    print("  docs/index.html  %6.1f KB  (replay of %d events)"
          % ((out_dir / "index.html").stat().st_size / 1024, len(events)))
    print("  docs/memo.html   %6.1f KB"
          % ((out_dir / "memo.html").stat().st_size / 1024))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", default=str(ROOT / "data" / "demo_catalog"))
    ap.add_argument("--royalties", default=None)
    ap.add_argument("--truth", default=None)
    ap.add_argument("--name", default="Meridian Sound Library")
    ap.add_argument("--multiple", type=float, default=None)
    ap.add_argument("--asking-price", type=float, default=None)
    ap.add_argument("--budget", type=int, default=0,
                    help="live calls allowed; 0 replays from cache for free")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args(argv)

    options = {
        "catalog": Path(args.catalog), "royalties": args.royalties,
        "truth": args.truth, "multiple": args.multiple,
        "asking_price": args.asking_price, "name": args.name,
        "mock": args.mock, "budget": args.budget,
    }

    events, result = record(options)
    if result is None or result.valuation is None:
        print("The audit produced no result; nothing to publish.",
              file=sys.stderr)
        return 1
    if result.mock_mode:
        print("That run was a mock. A recording of fabricated numbers is not "
              "worth publishing.", file=sys.stderr)
        return 1

    build(events, result, Path(args.out))
    print("\nCommit docs/ and enable GitHub Pages from the main branch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
