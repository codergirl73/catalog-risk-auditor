"""A local web UI for watching an audit happen.

The terminal run is honest but hard to read from the back of a room, and a
memo is the end of the story rather than the middle of it. This serves one
page that shows the plan, lights each step as the agent reaches it, streams
the events as they are emitted, and then hands over the memo.

Standard library only, like everything else: `http.server` for the server and
server-sent events for the stream, which is a documented HTTP feature rather
than a framework.

    python3 run.py data/demo_catalog --serve \\
        --royalties data/royalties.csv --truth data/ground_truth.csv

Deliberately local:

* It binds to 127.0.0.1. Nothing here is built to face a network, and an
  audit pane showing someone's catalog and its valuation is not a page to
  serve to a LAN by accident.
* The catalog path comes from the command line at startup, never from a
  request, so there is no path for a URL to reach the filesystem.
* Routes are an explicit table. There is no static file handler, so there is
  nothing to traverse out of.
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, memo
from .agent import AuditAgent

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# One audit at a time. Two concurrent runs would race on the same cache and
# on the credit budget, and there is one person watching.
_RUNNING = threading.Lock()

_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; frame-src 'self'; form-action 'none'; "
        "base-uri 'none'")


class AuditServer(ThreadingHTTPServer):
    """Holds the run configuration so no request has to supply it."""

    daemon_threads = True

    def __init__(self, address, handler, options: dict):
        super().__init__(address, handler)
        self.options = options
        self.last_result = None


def _sse(event: str, payload: dict) -> bytes:
    return ("event: %s\ndata: %s\n\n"
            % (event, json.dumps(payload))).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "catalog-risk-auditor"
    sys_version = ""

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt, *args):
        """Quiet by default; the page is the interface, not the log."""

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split("?")[0]
        routes = {
            "/": self.page,
            "/app.js": self.script,
            "/app.css": self.stylesheet,
            "/events": self.stream,
            "/memo": self.memo,
        }
        handler = routes.get(route)
        if handler is None:
            self._send(b"not found", "text/plain; charset=utf-8", 404)
            return
        handler()

    # -- routes ----------------------------------------------------------
    def page(self):
        self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def script(self):
        self._send(SCRIPT.encode("utf-8"),
                   "text/javascript; charset=utf-8")

    def stylesheet(self):
        self._send(STYLE.encode("utf-8"), "text/css; charset=utf-8")

    def memo(self):
        result = self.server.last_result
        if result is None:
            self._send(b"<p>No audit has run yet.</p>",
                       "text/html; charset=utf-8", 404)
            return
        try:
            body = memo.render(result, allow_mock=self.server.options["allow_mock"])
        except memo.MockModeRefusedError as exc:
            body = ("<p style='font:15px system-ui;padding:40px'>"
                    "<strong>Memo withheld.</strong><br>%s</p>" % exc)
        self._send(body.encode("utf-8"), "text/html; charset=utf-8")

    def stream(self):
        """Run the audit, emitting each Event as it is yielded."""
        if not _RUNNING.acquire(blocking=False):
            self._send(b"an audit is already running",
                       "text/plain; charset=utf-8", 409)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()

        options = self.server.options
        try:
            agent = AuditAgent(force_mock=options["mock"],
                               budget_limit=options["budget"])
            for event in agent.run(
                options["catalog"],
                royalties_csv=options["royalties"],
                truth_csv=options["truth"],
                multiple=options["multiple"],
                asking_price=options["asking_price"],
                catalog_name=options["name"],
            ):
                self.wfile.write(_sse(event.type, {
                    "title": event.title,
                    "detail": event.detail,
                    "data": {k: v for k, v in event.data.items()
                             if isinstance(v, (str, int, float, bool, list,
                                               dict, type(None)))},
                }))
                self.wfile.flush()

            self.server.last_result = agent.result
            self.wfile.write(_sse("summary", _summary(agent.result)))
        except (BrokenPipeError, ConnectionResetError):
            pass                       # the page was closed mid-run
        except Exception as exc:
            with contextlib.suppress(OSError):
                self.wfile.write(
                    _sse("failed", {"detail": config.redact(str(exc))}))
        finally:
            _RUNNING.release()


def _summary(result) -> dict:
    """The numbers the page shows large, once the run has finished."""
    if result is None or result.valuation is None:
        return {}
    val = result.valuation
    evaluation = asdict(result.evaluation) if result.evaluation else {}
    clean_revenue = max(0.0, val.annual_revenue_usd
                        - val.suspect_revenue_usd - val.contested_revenue_usd)
    return {
        "escrow": val.recommended_escrow_usd,
        "asking": val.asking_price_usd,
        "revenue": val.annual_revenue_usd,
        "tracks": val.track_count,
        "clean": val.clean_count,
        "contested": val.contested_count,
        "suspect": val.suspect_count,
        "errors": val.error_count,
        "clean_revenue": clean_revenue,
        "contested_revenue": val.contested_revenue_usd,
        "suspect_revenue": val.suspect_revenue_usd,
        "suspect_share_count": val.suspect_share_by_count * 100,
        "suspect_share_revenue": val.suspect_revenue_share * 100,
        "review_queue": result.review_queue[:10],
        "mock": result.mock_mode,
        "evaluation": evaluation,
    }


def options_from(args) -> dict:
    """The run configuration, as one value rather than ten parameters."""
    return {
        "catalog": Path(args.catalog),
        "royalties": args.royalties,
        "truth": args.truth,
        "multiple": args.multiple,
        "asking_price": args.asking_price,
        "name": args.name,
        "mock": args.mock,
        "allow_mock": args.allow_mock,
        "budget": args.budget,
    }


def serve(options: dict, port: int = DEFAULT_PORT) -> None:
    """Run the UI until interrupted. Blocks."""
    httpd = AuditServer((HOST, port), Handler, options)
    url = "http://%s:%d/" % (HOST, port)
    print("\n  Catalog Risk Auditor")
    print("  %s" % url)
    print("  bound to localhost only  |  ctrl-c to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")
    finally:
        httpd.server_close()


# ---------------------------------------------------------------------------
# the page
#
# Served from constants rather than files: the server has nothing to look up
# on disk at runtime, which is also why there is no static file handler to
# traverse out of.
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catalog Risk Auditor</title>
<link rel="stylesheet" href="/app.css">
</head><body>
<header>
  <div>
    <p class="eyebrow">Acquisition diligence</p>
    <h1>Catalog Risk Auditor</h1>
  </div>
  <button id="run">Run audit</button>
</header>

<main>
  <section class="panel" id="plan-panel">
    <h2>Plan</h2>
    <ol id="plan"><li class="waiting">waiting to start</li></ol>
    <div id="budget" class="budget hidden">
      <span class="k">Credits</span><span class="v" id="budget-v">&mdash;</span>
      <span class="k">Runtime</span><span class="v" id="runtime-v">&mdash;</span>
    </div>
  </section>

  <section class="panel wide">
    <h2>Live</h2>
    <div id="log"><p class="waiting">The agent states its plan, then works
      through it. Every escalation carries its reason.</p></div>
  </section>
</main>

<section class="panel result hidden" id="result">
  <div class="headline">
    <div>
      <p class="label">Recommended escrow</p>
      <p class="figure" id="escrow">&mdash;</p>
      <p class="sub" id="against"></p>
    </div>
    <a id="memo" href="/memo" target="_blank"
       rel="noopener noreferrer">Open the memo &rarr;</a>
  </div>

  <div class="bars">
    <p class="barlabel">By track count <span id="count-total"></span></p>
    <div class="bar" id="bar-count"></div>
    <p class="barlabel">By annual revenue <span id="rev-total"></span></p>
    <div class="bar" id="bar-rev"></div>
    <div class="legend">
      <span><i class="clean"></i>Clean</span>
      <span><i class="contested"></i>Contested</span>
      <span><i class="suspect"></i>Suspect</span>
      <span><i class="error"></i>Unscored</span>
    </div>
    <p class="note" id="contrast"></p>
  </div>

  <div id="accuracy" class="accuracy hidden">
    <h3>Accuracy against ground truth</h3>
    <p id="accuracy-text"></p>
  </div>

  <div id="queue-wrap" class="hidden">
    <h3>Human review queue</h3>
    <p class="note">What the agent declined to call, highest earning first.</p>
    <table id="queue"><tbody></tbody></table>
  </div>
</section>

<script src="/app.js"></script>
</body></html>"""


STYLE = """
:root{
  --bg:#12151a; --panel:#181c23; --line:#262c36; --ink:#e8ecf2;
  --ink2:#a8b3c2; --muted:#6f7c8d;
  /* Same risk scale as the memo: validated for deuteranopia separation,
     which green/amber/red is not. */
  --clean:#4f92d6; --contested:#d9a021; --suspect:#d4544a; --error:#5d6876;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif}
header{display:flex;align-items:center;justify-content:space-between;
  padding:26px 32px;border-bottom:1px solid var(--line)}
.eyebrow{font:11px ui-monospace,Menlo,monospace;letter-spacing:.16em;
  text-transform:uppercase;color:var(--contested);margin:0 0 4px}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
h2{font-size:11px;text-transform:uppercase;letter-spacing:.12em;
  color:var(--muted);margin:0 0 14px}
h3{font-size:13px;margin:22px 0 8px;color:var(--ink2)}
button{background:var(--clean);color:#08121c;border:0;border-radius:6px;
  padding:11px 22px;font-size:15px;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.08)}
button:disabled{background:var(--line);color:var(--muted);cursor:default}
main{display:grid;grid-template-columns:320px 1fr;gap:18px;padding:18px 32px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:20px 22px}
.result{margin:0 32px 40px}
.hidden{display:none}
.waiting{color:var(--muted);font-style:italic;margin:0}
ol#plan{margin:0;padding-left:0;list-style:none;counter-reset:s}
ol#plan li{counter-increment:s;padding:7px 0 7px 30px;position:relative;
  color:var(--muted);font-size:14px;border-bottom:1px solid var(--line)}
ol#plan li:last-child{border-bottom:0}
ol#plan li::before{content:counter(s);position:absolute;left:0;top:7px;
  width:19px;height:19px;border-radius:50%;border:1px solid var(--line);
  font-size:11px;line-height:18px;text-align:center;color:var(--muted)}
ol#plan li.active{color:var(--ink)}
ol#plan li.active::before{border-color:var(--contested);color:var(--contested)}
ol#plan li.done{color:var(--ink2)}
ol#plan li.done::before{content:"\\2713";border-color:var(--clean);
  color:var(--clean)}
.budget{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;
  margin-top:16px;padding-top:14px;border-top:1px solid var(--line);
  font-size:12.5px}
.budget .k{color:var(--muted)}
.budget .v{text-align:right;font-family:ui-monospace,Menlo,monospace;
  color:var(--ink2)}
#log{max-height:56vh;overflow-y:auto;font-size:14px}
.row{padding:7px 0;border-bottom:1px solid var(--line);display:flex;gap:12px}
.row:last-child{border-bottom:0}
.tag{flex:0 0 78px;font:10.5px ui-monospace,Menlo,monospace;
  text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  padding-top:3px}
.row .body{flex:1;min-width:0}
.row .t{font-weight:600}
.row .d{color:var(--ink2);font-size:13.5px;overflow-wrap:anywhere}
.row.finding .t,.row.verdict .t{color:var(--contested)}
.row.warn .tag,.row.warn .t{color:var(--suspect)}
.row.done .t{color:var(--clean)}
.headline{display:flex;align-items:flex-end;justify-content:space-between;
  gap:24px;flex-wrap:wrap}
.label{font-size:11px;text-transform:uppercase;letter-spacing:.12em;
  color:var(--muted);margin:0}
.figure{font-size:44px;font-weight:700;letter-spacing:-.02em;margin:4px 0 2px;
  font-variant-numeric:tabular-nums;color:var(--suspect)}
.sub{color:var(--ink2);margin:0;font-size:14px}
#memo{background:var(--clean);color:#08121c;text-decoration:none;
  padding:11px 20px;border-radius:6px;font-weight:600;white-space:nowrap}
.bars{margin-top:26px}
.barlabel{font-size:12.5px;color:var(--ink2);margin:16px 0 6px}
.barlabel span{color:var(--muted)}
.bar{display:flex;gap:2px;height:30px;border-radius:5px;overflow:hidden;
  background:var(--line)}
.bar div{display:flex;align-items:center;justify-content:center;
  font:11px/1 ui-monospace,Menlo,monospace;font-weight:700;color:#08121c}
.bar .clean{background:var(--clean)}
.bar .contested{background:var(--contested)}
.bar .suspect{background:var(--suspect)}
.bar .error{background:var(--error)}
.legend{display:flex;gap:18px;margin-top:12px;font-size:12px;
  color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
.legend i.clean{background:var(--clean)}
.legend i.contested{background:var(--contested)}
.legend i.suspect{background:var(--suspect)}
.legend i.error{background:var(--error)}
.note{color:var(--muted);font-size:13px;margin-top:14px}
#contrast{color:var(--contested);font-size:14.5px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
td{padding:7px 8px;border-bottom:1px solid var(--line);
  color:var(--ink2);vertical-align:top}
td.n{text-align:right;font-family:ui-monospace,Menlo,monospace;
  white-space:nowrap;color:var(--ink)}
.accuracy p{color:var(--ink2);font-size:14px;margin:0}
@media (max-width:900px){main{grid-template-columns:1fr}.figure{font-size:34px}}
"""


SCRIPT = """
'use strict';
const $ = (id) => document.getElementById(id);
const money = (n) => '$' + Math.round(n).toLocaleString('en-US');

let steps = [];

function renderPlan() {
  $('plan').innerHTML = steps.map((s) => '<li>' + s + '</li>').join('');
}

function markStep(title) {
  const items = $('plan').querySelectorAll('li');
  const at = steps.indexOf(title);
  if (at < 0) return;
  items.forEach((li, i) => {
    li.className = i < at ? 'done' : (i === at ? 'active' : '');
  });
}

function finishPlan() {
  $('plan').querySelectorAll('li').forEach((li) => { li.className = 'done'; });
}

function log(type, title, detail) {
  const row = document.createElement('div');
  row.className = 'row ' + type;
  const tag = document.createElement('div');
  tag.className = 'tag';
  tag.textContent = type.replace('tool_result', 'tool');
  const body = document.createElement('div');
  body.className = 'body';
  if (title) {
    const t = document.createElement('div');
    t.className = 't';
    t.textContent = title;
    body.appendChild(t);
  }
  if (detail) {
    const d = document.createElement('div');
    d.className = 'd';
    d.textContent = detail;
    body.appendChild(d);
  }
  row.appendChild(tag);
  row.appendChild(body);
  const box = $('log');
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

function bar(el, parts, total) {
  el.innerHTML = '';
  if (!total) { el.innerHTML = ''; return; }
  parts.forEach(([name, value]) => {
    if (value <= 0) return;
    const share = value / total;
    const seg = document.createElement('div');
    seg.className = name;
    seg.style.flex = String(Math.max(share, 0.004));
    seg.title = name + ': ' + value;
    if (share > 0.08) seg.textContent = Math.round(share * 100) + '%';
    el.appendChild(seg);
  });
}

function showSummary(s) {
  if (!s || !s.tracks) return;
  $('result').classList.remove('hidden');
  $('escrow').textContent = money(s.escrow);
  $('against').textContent =
    'against an asking price of ' + money(s.asking) +
    ' \\u00b7 ' + s.tracks + ' assets reviewed';

  $('count-total').textContent = '\\u2014 ' + s.tracks + ' assets';
  bar($('bar-count'), [['clean', s.clean], ['contested', s.contested],
                       ['suspect', s.suspect], ['error', s.errors]], s.tracks);

  $('rev-total').textContent = '\\u2014 ' + money(s.revenue);
  bar($('bar-rev'), [['clean', s.clean_revenue],
                     ['contested', s.contested_revenue],
                     ['suspect', s.suspect_revenue]], s.revenue);

  if (s.revenue > 0) {
    $('contrast').textContent =
      'Suspect assets are ' + s.suspect_share_count.toFixed(1) +
      '% of this catalog by count but ' + s.suspect_share_revenue.toFixed(1) +
      '% of its revenue. Only the second number changes the price.';
  }

  if (s.review_queue && s.review_queue.length) {
    $('queue-wrap').classList.remove('hidden');
    $('queue').querySelector('tbody').innerHTML = s.review_queue.map((q) => {
      const td = document.createElement('td');
      td.textContent = q.reason;
      const name = document.createElement('td');
      name.textContent = q.filename;
      return '<tr>' + name.outerHTML +
             '<td class="n">' + money(q.annual_usd) + '</td>' +
             td.outerHTML + '</tr>';
    }).join('');
  }

  if (s.mock) {
    $('memo').textContent = 'Memo withheld \\u2014 mock run';
  }
}

$('run').addEventListener('click', () => {
  const button = $('run');
  button.disabled = true;
  button.textContent = 'Auditing\\u2026';
  $('log').innerHTML = '';
  $('result').classList.add('hidden');
  $('accuracy').classList.add('hidden');
  $('queue-wrap').classList.add('hidden');

  const src = new EventSource('/events');
  const simple = ['step', 'tool_call', 'tool_result', 'finding', 'verdict',
                  'warn', 'error'];

  src.addEventListener('plan', (e) => {
    const ev = JSON.parse(e.data);
    steps = (ev.data && ev.data.steps) || [];
    renderPlan();
    $('budget').classList.remove('hidden');
    log('plan', ev.title, ev.detail);
  });

  simple.forEach((type) => src.addEventListener(type, (e) => {
    const ev = JSON.parse(e.data);
    if (type === 'step') markStep(ev.title);
    if (ev.title === 'budget check') $('budget-v').textContent = ev.detail;
    if (ev.title === 'runtime estimate') {
      $('runtime-v').textContent = ev.detail.split(' at ')[0];
    }
    if (ev.title === 'Accuracy against ground truth') {
      $('accuracy').classList.remove('hidden');
      $('accuracy-text').textContent = ev.detail;
    }
    log(type, ev.title, ev.detail);
  }));

  src.addEventListener('done', (e) => {
    const ev = JSON.parse(e.data);
    finishPlan();
    log('done', ev.title, ev.detail);
  });

  src.addEventListener('summary', (e) => {
    showSummary(JSON.parse(e.data));
    src.close();
    button.disabled = false;
    button.textContent = 'Run again';
  });

  src.addEventListener('failed', (e) => {
    log('warn', 'Run failed', JSON.parse(e.data).detail);
    src.close();
    button.disabled = false;
    button.textContent = 'Try again';
  });

  src.onerror = () => {
    src.close();
    button.disabled = false;
    button.textContent = 'Run audit';
  };
});
"""
