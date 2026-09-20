"""The acquisition risk memo.

The output a buyer actually reads. It has to survive being opened by someone
with ninety seconds, no interest in machine learning, and a number to
negotiate. Headline first, detail underneath, method last.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .evaluation import summary as eval_summary
from .models import AuditResult, Tier
from .valuation import headline


class MockModeRefused(Exception):
    """Raised rather than quietly producing a memo full of fake numbers."""


_CSS = """
:root{--paper:#f1f3f6;--surface:#fff;--ink:#14181f;--ink2:#3e4854;--muted:#6b7683;
--line:#d5dce4;--soft:#e8edf2;--ok:#1d6350;--hold:#6a5a1c;--risk:#9c2f24;--accent:#9c5d0c}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);padding:40px 16px;
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.sheet{max-width:860px;margin:0 auto;background:var(--surface);
border:1px solid var(--line);padding:48px 52px}
.eyebrow{font:11px ui-monospace,Menlo,monospace;letter-spacing:.14em;
text-transform:uppercase;color:var(--accent);margin:0 0 10px}
h1{font-size:28px;letter-spacing:-.02em;margin:0 0 6px}
.sub{color:var(--muted);margin:0 0 26px;font-size:14px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);
margin:34px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.head{border:2px solid var(--risk);padding:20px 22px;margin:22px 0}
.head .n{font-size:32px;font-weight:700;letter-spacing:-.02em;
font-variant-numeric:tabular-nums;color:var(--risk);margin:0 0 6px}
.head p{margin:0;font-size:14.5px;color:var(--ink2)}
.kv{display:grid;grid-template-columns:1fr auto;gap:0}
.kv div{padding:8px 0;border-bottom:1px dotted var(--line);font-size:14.5px}
.kv .v{text-align:right;font-family:ui-monospace,Menlo,monospace;
font-variant-numeric:tabular-nums;font-weight:500}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
color:var(--muted);padding:7px 8px;border-bottom:1px solid var(--line)}
td{padding:6px 8px;border-bottom:1px solid var(--soft);
font-variant-numeric:tabular-nums}
td.n{text-align:right}
.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:2px;
text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.pill.clean{background:#e0efea;color:var(--ok)}
.pill.contested{background:#f5efd9;color:var(--hold)}
.pill.suspect{background:#f9e6e3;color:var(--risk)}
.pill.error{background:#eceff3;color:var(--muted)}
.banner{background:#f9e6e3;border:2px solid var(--risk);padding:14px 16px;
margin-bottom:24px;font-size:14px;color:var(--risk);font-weight:600}
.note{font-size:13px;color:var(--muted);margin-top:14px;font-style:italic}
.foot{margin-top:38px;padding-top:16px;border-top:1px solid var(--line);
font-size:12px;color:var(--muted);line-height:1.7}
.wrapt{overflow-x:auto}
@media print{body{padding:0;background:#fff}.sheet{border:0;padding:0;max-width:none}}
"""


def _tier_pill(tier: Tier) -> str:
    return '<span class="pill %s">%s</span>' % (tier.value, tier.value)


def render(result: AuditResult, allow_mock: bool = False) -> str:
    """Build the memo HTML.

    Refuses to render a mock-mode run unless explicitly overridden, so a
    meaningless number cannot end up in front of a buyer or a judge by
    accident.
    """
    if result.mock_mode and not allow_mock:
        raise MockModeRefused(
            "This run used the mock detector, so every score is fabricated. "
            "Set HS_API_KEY and re-run, or pass --allow-mock if you genuinely "
            "want a memo built on meaningless numbers."
        )

    e = html.escape
    val = result.valuation
    banner = ""
    if result.mock_mode:
        banner = ('<div class="banner">MOCK DATA &mdash; every score in this '
                  'document is fabricated and must not be relied on.</div>')

    rows = []
    ranked = sorted(
        result.assets,
        key=lambda a: (-(a.annual_usd), -(a.ai_score)),
    )
    for a in ranked[:60]:
        score = "%.0f" % a.ai_score if a.ai_score >= 0 else "&mdash;"
        title = a.royalty.title if a.royalty and a.royalty.title else a.filename
        artist = a.royalty.artist if a.royalty else ""
        rows.append(
            "<tr><td>%s</td><td>%s</td><td class='n'>%s</td>"
            "<td class='n'>$%s</td><td>%s</td></tr>"
            % (e(title[:52]), e(artist[:28]), score,
               f"{a.annual_usd:,.0f}", _tier_pill(a.tier))
        )
    more = ""
    if len(ranked) > 60:
        more = ("<p class='note'>Showing the 60 highest-earning assets of %d. "
                "Full detail in audit.json.</p>" % len(ranked))

    review_rows = []
    for item in result.review_queue[:40]:
        review_rows.append(
            "<tr><td>%s</td><td class='n'>%s</td><td class='n'>$%s</td>"
            "<td>%s</td></tr>"
            % (e(item["filename"][:52]),
               "%.0f" % item["ai_score"] if item["ai_score"] >= 0 else "&mdash;",
               f"{item['annual_usd']:,.0f}", e(item["reason"]))
        )
    review_section = ""
    if review_rows:
        review_section = """
        <h2>Human review queue &mdash; %d assets</h2>
        <p>These could not be settled from audio alone. They are excluded from
        the clean base and only partially weighted in the escrow figure.</p>
        <div class="wrapt"><table>
          <thead><tr><th>Asset</th><th class="n">Score</th>
          <th class="n">Annual</th><th>Why</th></tr></thead>
          <tbody>%s</tbody></table></div>""" % (
            len(result.review_queue), "".join(review_rows))

    eval_section = ""
    if result.evaluation and result.evaluation.labelled:
        ev = result.evaluation
        eval_section = """
        <h2>Audit accuracy against ground truth</h2>
        <p>%s</p>
        <div class="kv">
          <div>Labelled assets</div><div class="v">%d</div>
          <div>Correctly flagged AI</div><div class="v">%d</div>
          <div>Missed AI</div><div class="v">%d</div>
          <div>Human tracks wrongly flagged</div><div class="v">%d</div>
          <div>Sent to human review instead of guessing</div><div class="v">%d</div>
          <div>Precision</div><div class="v">%s</div>
          <div>Recall</div><div class="v">%s</div>
        </div>
        <p class="note">This catalog was constructed with known labels so the
        error rate could be measured. A real acquisition has no answer key,
        which is why the contested band exists.</p>""" % (
            e(eval_summary(ev)), ev.labelled, ev.true_positive,
            ev.false_negative, ev.false_positive,
            ev.contested_ai + ev.contested_human,
            "%.2f" % ev.precision if ev.precision is not None else "&mdash;",
            "%.2f" % ev.recall if ev.recall is not None else "&mdash;",
        )

    generated = datetime.fromtimestamp(
        result.generated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acquisition risk memo &mdash; %s</title><style>%s</style></head>
<body><div class="sheet">
%s
<p class="eyebrow">Acquisition risk memo</p>
<h1>%s</h1>
<p class="sub">Prepared for the acquiring party &middot; %d assets reviewed</p>

<div class="head">
  <p class="n">$%s</p>
  <p>%s</p>
</div>

<h2>Position</h2>
<div class="kv">
  <div>Tracks in catalog</div><div class="v">%d</div>
  <div>Reported annual royalties</div><div class="v">$%s</div>
  <div>Acquisition multiple</div><div class="v">%.1f&times;</div>
  <div>Asking price</div><div class="v">$%s</div>
  <div>Clean</div><div class="v">%d</div>
  <div>Contested &mdash; human review</div><div class="v">%d</div>
  <div>Suspect</div><div class="v">%d</div>
  <div>Detection failed</div><div class="v">%d</div>
  <div>Revenue on suspect assets</div><div class="v">$%s (%.1f%%)</div>
  <div>Revenue on contested assets</div><div class="v">$%s (%.1f%%)</div>
  <div>Recommended escrow</div><div class="v">$%s</div>
</div>
<p class="note">%s</p>

<h2>Assets by revenue</h2>
<div class="wrapt"><table>
  <thead><tr><th>Title</th><th>Artist</th><th class="n">AI score</th>
  <th class="n">Annual</th><th>Tier</th></tr></thead>
  <tbody>%s</tbody></table></div>
%s

%s
%s

<h2>Method</h2>
<p>Every asset was scored through the HumanStandard detection API. Thresholds
applied: %s. Assets scoring below the detector's own confidence floor are
routed to human review regardless of score. Suspect revenue is escrowed in
full; contested revenue is weighted at %.0f%% because uncertainty is not a
finding.</p>

<div class="foot">
  Generated %s &middot; detector: %s &middot; evidence manifest %s<br>
  This memo is an audio-authenticity assessment, not a legal opinion or a
  valuation. Copyright enforceability of AI-generated works is a question for
  counsel.
</div>
</div></body></html>""" % (
        e(result.catalog_name), _CSS, banner, e(result.catalog_name),
        len(result.assets),
        f"{val.recommended_escrow_usd:,.0f}", e(headline(val)),
        val.track_count, f"{val.annual_revenue_usd:,.0f}", val.multiple,
        f"{val.asking_price_usd:,.0f}",
        val.clean_count, val.contested_count, val.suspect_count, val.error_count,
        f"{val.suspect_revenue_usd:,.0f}", val.suspect_revenue_share * 100,
        f"{val.contested_revenue_usd:,.0f}", val.contested_revenue_share * 100,
        f"{val.recommended_escrow_usd:,.0f}",
        e(val.escrow_basis),
        "".join(rows), more, review_section, eval_section,
        e(config.thresholds_summary()), config.CONTESTED_ESCROW_WEIGHT * 100,
        generated, e(result.provider), e(result.manifest_sha256[:32]),
    )


def write(result: AuditResult, allow_mock: bool = False) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / "risk_memo.html"
    path.write_text(render(result, allow_mock=allow_mock), encoding="utf-8")
    return path
