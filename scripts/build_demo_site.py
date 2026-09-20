#!/usr/bin/env python3
"""Build a static, shareable replay of a real audit.

The live UI needs Python running, so it cannot be a link somebody clicks from
a submission page. This records one real run and renders it as a static page
that replays the same events with the same visuals, next to the memo it
produced.

Nothing is recreated or dramatised: the events are the ones the agent emitted,
the numbers are the ones it computed, and the raw JSON on the evidence slide
is exactly what HumanStandard returned for that track. The run is replayed
from the response cache, so building the site spends no credits.

The viewer drives it. The recording is cut into slides -- one per plan step,
plus an evidence slide showing every verdict that came back and one response
in full -- and nothing advances on its own.

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
from catalog_audit.agent import PLAN, AuditAgent  # noqa: E402

# Populations the API names when it is declining to attribute rather than
# naming a generator. Same list the memo and the tiering reasons use.
NOT_A_GENERATOR = frozenset({"human", "uncertain", "unknown", "none"})

# Tier order for the evidence table: the rows a buyer cares about first.
TIER_ORDER = {"suspect": 0, "contested": 1, "error": 2, "clean": 3}


def group_events(events: list) -> list:
    """Cut the flat event stream into one group per declared plan step.

    Everything before the first step -- the plan announcement itself -- forms
    its own opening group, so the first click reveals the agent stating what
    it is about to do rather than jumping straight into doing it.
    """
    groups, prelude, index = [], [], 0
    while index < len(events) and events[index]["type"] != "step":
        prelude.append(events[index])
        index += 1
    if prelude:
        groups.append({"title": "", "events": prelude})

    current = None
    for event in events[index:]:
        if event["type"] == "step":
            if current:
                groups.append(current)
            current = {"title": event["title"], "events": [event]}
        elif current is not None:
            current["events"].append(event)
    if current:
        groups.append(current)
    return groups


def track_rows(result) -> list:
    """Every verdict that came back, as a table a viewer can read.

    The streamed events say "16/16 scored". They do not say what any of the
    sixteen actually were, which is the part somebody evaluating a detection
    tool wants to see.
    """
    rows = []
    for asset in result.assets:
        score = asset.score
        origin = (score.origin or "") if score else ""
        rows.append({
            "filename": asset.filename,
            "tier": asset.tier.value,
            "verdict": (score.verdict if score else "") or "",
            "confidence": round(score.confidence, 3) if score else None,
            "ai_score": round(score.ai_score, 1) if score and score.ok else None,
            "origin": "" if origin.lower() in NOT_A_GENERATOR else origin,
            "label": (score.industry_label if score else "") or "",
            "error": (score.error if score else "") or "",
            # The response that produced this row, so any track can be opened
            # in full rather than only the one chosen as the spotlight.
            "response": (score.raw.get("response")
                         if score and isinstance(score.raw, dict)
                         and not score.mock else None),
            "tier_verdicts": (score.tier_verdicts or {}) if score else {},
        })
    rows.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 9), r["filename"]))
    return rows


def pick_spotlight(result):
    """One real response to show in full, chosen for what it teaches.

    Preferring a track whose calibrated operating points disagree with its
    own headline verdict: that is the case the whole tiering design exists
    for, and it is far more convincing seen in the raw JSON than described.
    """
    candidates = [
        a for a in result.assets
        if a.score and a.score.ok and not a.score.mock
        and isinstance(a.score.raw, dict)
        and isinstance(a.score.raw.get("response"), dict)
    ]
    if not candidates:
        return None

    def split_verdict(asset) -> bool:
        values = {v for v in (asset.score.tier_verdicts or {}).values() if v}
        return len(values) > 1

    chosen = next((a for a in candidates if split_verdict(a)), candidates[0])
    return {
        "filename": chosen.filename,
        "tier": chosen.tier.value,
        "verdict": chosen.score.verdict,
        "tier_verdicts": chosen.score.tier_verdicts or {},
        "response": chosen.score.raw["response"],
    }


def build_slides(events: list) -> list:
    """The recording, cut into slides the viewer advances through."""
    slides = [{"kind": "events", "title": group["title"],
               "events": group["events"]}
              for group in group_events(events)]

    # The evidence belongs immediately after scoring, which is the moment it
    # was produced. Matched on the step's own text rather than an index, so
    # rewording the plan cannot silently misplace it.
    scoring = next((i for i, s in enumerate(slides)
                    if s["title"] == PLAN[1]), None)
    evidence = {"kind": "evidence", "title": "What HumanStandard returned"}
    if scoring is None:
        slides.append(evidence)
    else:
        slides.insert(scoring + 1, evidence)

    slides.append({"kind": "summary", "title": "The position"})
    return slides


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
  Every event below was emitted by the agent, every number was computed by it,
  and the raw JSON further down is exactly what HumanStandard returned. Step
  through it with the controls at the bottom &mdash; nothing moves on its own
  &mdash; or <a href="memo.html">read the memo it produced</a>.
</div>
"""

EVIDENCE_HTML = """
<section class="panel evidence hidden" id="evidence">
  <h2>What HumanStandard returned</h2>
  <p class="note">The stream above says &ldquo;16/16 scored&rdquo;. It does not
  say what any of the sixteen were. These are the verdicts themselves, worst
  first &mdash; one row per real API response.</p>
  <div class="wrapt">
    <table class="tracks">
      <thead><tr>
        <th>Track</th><th>Tier</th><th>Verdict</th>
        <th class="n">Confidence</th><th class="n">AI score</th>
        <th>Attribution</th>
      </tr></thead>
      <tbody id="tracks-body"></tbody>
    </table>
  </div>

  <div class="spotlight hidden" id="spotlight-wrap">
    <h3>The response, in full</h3>
    <p class="note">Click any row above to read exactly what came back for
    that track. Nothing is reordered or trimmed.</p>
    <p class="spotfile" id="spotlight-file"></p>
    <p class="note" id="spotlight-note"></p>
    <pre id="spotlight-json"></pre>
  </div>
</section>
"""

NAV_HTML = """
<nav class="replay-nav">
  <button id="prev" disabled>&larr; Back</button>
  <span id="nav-pos">__SLIDE_COUNT__ slides</span>
  <button id="next">Start &rarr;</button>
</nav>
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

.evidence{margin:0 32px 18px}
.evidence h3{margin:0 0 6px}
table.tracks{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
table.tracks th{text-align:left;font-size:10.5px;text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted);padding:7px 8px;
  border-bottom:1px solid var(--line);white-space:nowrap}
table.tracks tbody tr{cursor:pointer}
table.tracks tbody tr:hover td{background:#1b212b}
table.tracks tbody tr.selected td{background:#1e2732;
  box-shadow:inset 2px 0 0 var(--contested)}
table.tracks td{padding:7px 8px;border-bottom:1px solid var(--line);
  color:var(--ink2);vertical-align:middle}
table.tracks td.n{text-align:right;font-family:ui-monospace,Menlo,monospace;
  color:var(--ink);white-space:nowrap}
table.tracks td.file{font-family:ui-monospace,Menlo,monospace;font-size:12px;
  overflow-wrap:anywhere}
.pill{display:inline-block;font-size:10.5px;padding:2px 8px;border-radius:3px;
  text-transform:uppercase;letter-spacing:.05em;font-weight:700;
  white-space:nowrap}
.pill.clean{background:#16314c;color:var(--clean)}
.pill.contested{background:#453612;color:var(--contested)}
.pill.suspect{background:#4a1e19;color:var(--suspect)}
.pill.error{background:#2a303a;color:var(--error)}
.tag{display:inline-block;font-size:10.5px;padding:2px 7px;border-radius:3px;
  background:#1c2733;color:var(--ink2);letter-spacing:.03em}
.spotlight{margin-top:24px;padding-top:18px;border-top:1px solid var(--line)}
.spotfile{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;
  color:var(--ink);margin:0 0 8px;overflow-wrap:anywhere}
#spotlight-json{background:#0d1015;border:1px solid var(--line);
  border-radius:8px;padding:14px 16px;margin-top:12px;
  font:12.5px/1.6 ui-monospace,Menlo,monospace;color:var(--ink2);
  max-height:420px;overflow:auto;white-space:pre}
#spotlight-json span{display:block}
#spotlight-json .hit{background:#3a2f0d;color:var(--contested)}
.replay-nav{position:sticky;bottom:0;z-index:5;display:flex;
  align-items:center;justify-content:space-between;gap:16px;
  padding:14px 32px;background:rgba(18,21,26,.94);
  border-top:1px solid var(--line)}
.replay-nav button{background:var(--clean);color:#08121c;border:0;
  border-radius:6px;padding:10px 20px;font-size:14px;font-weight:600;
  cursor:pointer}
.replay-nav button:hover:not(:disabled){filter:brightness(1.08)}
.replay-nav button#prev{background:transparent;color:var(--ink2);
  border:1px solid var(--line)}
.replay-nav button:disabled{opacity:.4;cursor:default}
#nav-pos{color:var(--muted);font-size:12.5px;
  font-family:ui-monospace,Menlo,monospace;text-align:center;flex:1}
@media (max-width:900px){.evidence{margin:0 16px 18px}
  .replay-nav{padding:12px 16px}}
"""

REPLAY_JS = r"""
'use strict';
const SLIDES = __SLIDES__;
const SUMMARY = __SUMMARY__;
const TRACKS = __TRACKS__;

let current = -1;

const TIER_LABEL = {clean: 'Clean', contested: 'Contested',
                    suspect: 'Suspect', error: 'Unscored'};

function cell(text, cls) {
  const td = document.createElement('td');
  if (cls) td.className = cls;
  td.textContent = text;
  return td;
}

function renderTracks() {
  const body = $('tracks-body');
  body.innerHTML = '';
  TRACKS.forEach((t, i) => {
    const tr = document.createElement('tr');
    tr.appendChild(cell(t.filename, 'file'));

    const tier = document.createElement('td');
    const pill = document.createElement('span');
    pill.className = 'pill ' + t.tier;
    pill.textContent = TIER_LABEL[t.tier] || t.tier;
    tier.appendChild(pill);
    tr.appendChild(tier);

    tr.appendChild(cell(t.error ? 'failed' : (t.verdict || '\u2014')));
    tr.appendChild(cell(t.confidence == null ? '\u2014'
      : Math.round(t.confidence * 100) + '%', 'n'));
    tr.appendChild(cell(t.ai_score == null ? '\u2014'
      : t.ai_score.toFixed(1), 'n'));

    const attribution = document.createElement('td');
    if (t.origin) {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = t.origin;
      attribution.appendChild(tag);
    } else {
      attribution.textContent = '\u2014';
    }
    tr.appendChild(attribution);

    if (t.response) {
      tr.addEventListener('click', () => selectTrack(i));
    } else {
      tr.style.cursor = 'default';
    }
    body.appendChild(tr);
  });
}

function selectTrack(index) {
  const track = TRACKS[index];
  if (!track || !track.response) return;

  const rows = $('tracks-body').querySelectorAll('tr');
  rows.forEach((row, i) => row.classList.toggle('selected', i === index));

  const wrap = $('spotlight-wrap');
  wrap.classList.remove('hidden');
  $('spotlight-file').textContent = track.filename;

  const tiers = track.tier_verdicts || {};
  const names = Object.keys(tiers);
  const pairs = names.map((k) => k + ' = ' + tiers[k]).join(', ');
  const distinct = new Set(names.map((k) => tiers[k]));
  if (!names.length) {
    $('spotlight-note').textContent =
      'This response carries no tier_verdicts, so the tiering fell back to ' +
      'the detector\u2019s own verdict field.';
  } else if (distinct.size > 1) {
    $('spotlight-note').textContent =
      'Read the verdict field first: it says "' + track.verdict +
      '". Now read tier_verdicts, the three calibrated operating points: ' +
      pairs + '. They disagree with it. This tool tiers on those, not on the ' +
      'headline field, which is why this track was caught rather than passed ' +
      'into the clean base.';
  } else {
    $('spotlight-note').textContent =
      'The verdict says "' + track.verdict + '" and all three calibrated ' +
      'operating points agree: ' + pairs + '. Nothing here is borderline.';
  }

  // Shown complete and unaltered -- no keys reordered, no arrays trimmed --
  // but tier_verdicts sits near the end behind a long risk_segments array,
  // so the pane opens scrolled to it.
  const pretty = JSON.stringify(track.response, null, 2);
  const pre = $('spotlight-json');
  pre.textContent = '';
  let target = null;
  pretty.split('\n').forEach((line) => {
    // One block element per line, carrying no newline of its own: an
    // inline-block that ends in \n eats the line that follows it.
    const row = document.createElement('span');
    row.textContent = line;
    if (line.indexOf('"tier_verdicts"') !== -1) {
      row.className = 'hit';
      target = row;
    }
    pre.appendChild(row);
  });
  pre.scrollTop = target
    ? Math.max(0, target.offsetTop - pre.offsetTop - 28) : 0;
}

function defaultTrack() {
  // Open on the track that teaches the most: one whose calibrated operating
  // points disagree with its own headline verdict.
  let fallback = -1;
  for (let i = 0; i < TRACKS.length; i++) {
    if (!TRACKS[i].response) continue;
    if (fallback < 0) fallback = i;
    const values = new Set(Object.values(TRACKS[i].tier_verdicts || {}));
    if (values.size > 1) return i;
  }
  return fallback;
}

function renderSlide(index) {
  // Rebuilt from slide zero every time rather than mutated forward, so going
  // back lands on exactly the state that slide had going forward.
  $('log').innerHTML = '';
  $('accuracy').classList.add('hidden');
  $('queue-wrap').classList.add('hidden');
  $('evidence').classList.add('hidden');
  $('result').classList.add('hidden');
  $('budget-v').textContent = '\u2014';
  $('runtime-v').textContent = '\u2014';
  steps = [];
  renderPlan();

  let reached = null;
  for (let i = 0; i <= index; i++) {
    const slide = SLIDES[i];
    if (slide.kind !== 'events') continue;
    slide.events.forEach((ev) => {
      if (ev.type === 'plan' && !steps.length) {
        steps = (ev.data && ev.data.steps) || [];
        renderPlan();
        $('budget').classList.remove('hidden');
      }
      if (ev.type === 'step') reached = ev.title;
      if (ev.title === 'budget check') $('budget-v').textContent = ev.detail;
      if (ev.title === 'runtime estimate') {
        $('runtime-v').textContent = ev.detail.split(' at ')[0];
      }
      if (ev.title === 'Accuracy against ground truth') {
        $('accuracy').classList.remove('hidden');
        $('accuracy-text').textContent = ev.detail;
      }
      log(ev.type, ev.title, ev.detail);
    });
  }
  if (reached) markStep(reached);

  const slide = SLIDES[index];
  if (slide.kind === 'evidence') {
    // Unhide before rendering: offsetTop inside a display:none element is
    // zero, so the scroll-to-field below would silently do nothing.
    $('evidence').classList.remove('hidden');
    renderTracks();
    const pick = defaultTrack();
    if (pick >= 0) {
      selectTrack(pick);
    } else {
      $('spotlight-wrap').classList.add('hidden');
    }
  } else if (slide.kind === 'summary') {
    finishPlan();
    showSummary(SUMMARY);
  }

  $('nav-pos').textContent =
    (index + 1) + ' / ' + SLIDES.length + '  \u00b7  ' + (slide.title || 'Plan');
  $('prev').disabled = index <= 0;
  $('next').textContent =
    index >= SLIDES.length - 1 ? 'Start over' : 'Next \u2192';
}

function go(delta) {
  if (delta > 0 && current >= SLIDES.length - 1) {
    current = 0;
  } else {
    current = Math.max(0, Math.min(SLIDES.length - 1, current + delta));
  }
  renderSlide(current);
}

$('next').addEventListener('click', () => go(1));
$('prev').addEventListener('click', () => go(-1));
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); go(1); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
});
"""


def build(events: list, result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    slides = build_slides(events)

    # The replay reuses the live UI's markup and stylesheet, so the recording
    # and the running tool look like the same thing, because they are.
    page = webui.PAGE
    page = page.replace('<link rel="stylesheet" href="/app.css">',
                        "<style>%s%s</style>" % (webui.STYLE, EXTRA_CSS))
    page = page.replace('<script src="/app.js"></script>', "")
    page = page.replace("<main>", BANNER + "<main>")
    page = page.replace("</main>", "</main>\n" + EVIDENCE_HTML)
    page = page.replace('<a id="memo" href="/memo" target="_blank"',
                        '<a id="memo" href="memo.html" target="_blank"')
    page = page.replace("<title>Catalog Risk Auditor</title>",
                        "<title>Catalog Risk Auditor &mdash; recorded run</title>")
    # Navigation lives in the sticky bar at the bottom, so the header's own
    # run button has nothing left to do.
    page = page.replace('<button id="run">Run audit</button>', "")

    shared = webui.SCRIPT.split("$('run').addEventListener")[0]
    replay = (REPLAY_JS
              .replace("__SLIDES__", json.dumps(slides))
              .replace("__SUMMARY__", json.dumps(webui._summary(result)))
              .replace("__TRACKS__", json.dumps(track_rows(result)))
              )

    footer = (
        '<footer>Recorded %s &middot; detector %s &middot; evidence manifest '
        '%s<br>Source: <a href="https://github.com/codergirl73/'
        'catalog-risk-auditor">github.com/codergirl73/catalog-risk-auditor</a>'
        ' &middot; the catalog and its royalty figures are constructed for '
        'demonstration; the detection results are real.</footer>'
        % (html.escape(str(result.catalog_name)), html.escape(result.provider),
           html.escape(result.manifest_sha256[:16])))

    nav = NAV_HTML.replace("__SLIDE_COUNT__", str(len(slides)))
    page = page.replace("</body>", "%s%s<script>%s\n%s</script></body>"
                        % (footer, nav, shared, replay))
    (out_dir / "index.html").write_text(page, encoding="utf-8")

    (out_dir / "memo.html").write_text(
        memo.render(result), encoding="utf-8")

    # Tell Pages not to run the content through Jekyll.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    spotlight = pick_spotlight(result)
    print("  docs/index.html  %6.1f KB  (%d slides, %d events, %d verdicts)"
          % ((out_dir / "index.html").stat().st_size / 1024, len(slides),
             len(events), len(result.assets)))
    print("  docs/memo.html   %6.1f KB" %
          ((out_dir / "memo.html").stat().st_size / 1024))
    if spotlight:
        print("  raw response shown: %s (verdict %s, tiers %s)"
              % (spotlight["filename"], spotlight["verdict"],
                 spotlight["tier_verdicts"]))
    else:
        print("  no live response available to show in full")


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
