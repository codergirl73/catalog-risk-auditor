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
--line:#d5dce4;--soft:#e8edf2;--accent:#9c5d0c;
/* Risk scale. Validated for deuteranopia/protanopia separation: the
   conventional green/amber/red reads as one colour to a red-green
   colourblind buyer, which is unusable in a document whose entire job is
   ranking risk. Blue/amber/red separates at every CVD type, and every
   segment is labelled so colour is never the only signal. */
--ok:#2f6fb0;--hold:#8a6000;--risk:#a32b22;
--ok-fill:#2f6fb0;--hold-fill:#c58a00;--risk-fill:#a32b22}
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
.pill.clean{background:#e3edf8;color:var(--ok)}
.pill.contested{background:#f7eed6;color:var(--hold)}
.pill.suspect{background:#f9e5e3;color:var(--risk)}
.pill.error{background:#eceff3;color:var(--muted)}
.banner{background:#f9e6e3;border:2px solid var(--risk);padding:14px 16px;
margin-bottom:24px;font-size:14px;color:var(--risk);font-weight:600}
.note{font-size:13px;color:var(--muted);margin-top:14px;font-style:italic}
.foot{margin-top:38px;padding-top:16px;border-top:1px solid var(--line);
font-size:12px;color:var(--muted);line-height:1.7}
.wrapt{overflow-x:auto}
figure{margin:18px 0 0}
figcaption{font-size:13px;color:var(--ink2);margin-bottom:12px}
.chart{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin-top:10px;font-size:12px;
color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
.ev{font-size:12px;color:var(--muted);word-break:break-all}
.tag{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:2px;
background:var(--soft);color:var(--ink2);letter-spacing:.03em}
@media print{body{padding:0;background:#fff}.sheet{border:0;padding:0;max-width:none}}
"""


def _tier_pill(tier: Tier) -> str:
    return '<span class="pill %s">%s</span>' % (tier.value, tier.value)


# Fills for the risk scale, matching the CSS tokens above.
_FILL = {"clean": "#2f6fb0", "contested": "#c58a00",
         "suspect": "#a32b22", "error": "#9aa4b0"}


def _bar(parts, y, width=720.0, height=30.0) -> str:
    """One stacked bar. parts is [(label, value, share)] with shares summing to 1.

    Segments are separated by a 2px gap in the surface colour rather than a
    stroke, so no segment's area is inflated by its own border, and the whole
    bar is clipped to a rounded rect so only the outer ends round.
    """
    clip = "clip%d" % int(y)
    out = ['<clipPath id="%s"><rect x="0" y="%.1f" width="%.1f" height="%.1f" '
           'rx="4"/></clipPath>' % (clip, y, width, height),
           '<g clip-path="url(#%s)">' % clip]

    x = 0.0
    for label, value, share in parts:
        if share <= 0:
            continue
        # A 2px floor keeps a one-track segment perceptible. On a 720px axis
        # that is 0.3%, which is below the resolution of the eye and of the
        # numbers printed beside it.
        w = max(2.0, share * width)
        out.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s">'
            '<title>%s: %s (%.1f%%)</title></rect>'
            % (x, y, w, height, _FILL.get(label, "#999"),
               html.escape(label), html.escape(str(value)), share * 100))
        if w > 52:
            out.append(
                '<text x="%.1f" y="%.1f" fill="#fff" font-size="12" '
                'font-weight="600" text-anchor="middle" '
                'font-family="ui-monospace,Menlo,monospace">%.0f%%</text>'
                % (x + w / 2, y + height / 2 + 4, share * 100))
        # the gap is drawn by the next segment starting 2px later
        x += w + 2.0
    out.append("</g>")
    return "".join(out)


def _exposure_chart(val) -> str:
    """The count/revenue contrast, which is the whole argument of the tool.

    Synthetic uploads accumulate far faster than they earn, so the two bars
    are shaped differently -- and a reader sees that in about a second, which
    no sentence achieves.
    """
    counts = [("clean", val.clean_count), ("contested", val.contested_count),
              ("suspect", val.suspect_count), ("error", val.error_count)]
    total_n = sum(n for _, n in counts) or 1

    rev = [("clean", val.annual_revenue_usd - val.suspect_revenue_usd
            - val.contested_revenue_usd),
           ("contested", val.contested_revenue_usd),
           ("suspect", val.suspect_revenue_usd),
           ("error", 0.0)]
    total_rev = sum(max(0.0, r) for _, r in rev)

    if not total_rev:
        rev_row = ('<text x="0" y="112" fill="#6b7683" font-size="12.5" '
                   'font-family="-apple-system,Helvetica,Arial,sans-serif">'
                   'No revenue sheet supplied \u2014 exposure by count only.'
                   '</text>')
    else:
        rev_row = _bar([(k, "$%s" % f"{max(0.0, v):,.0f}",
                         max(0.0, v) / total_rev) for k, v in rev], 96.0)

    label = ('<text x="0" y="%.1f" fill="#3e4854" font-size="12.5" '
             'font-weight="600" '
             'font-family="-apple-system,Helvetica,Arial,sans-serif">%s</text>')

    legend = "".join(
        '<span><i style="background:%s"></i>%s</span>'
        % (_FILL[k], k.capitalize() + (" (unscored)" if k == "error" else ""))
        for k, n in counts if n or k != "error")

    return """
    <figure>
      <figcaption>Suspect assets are typically a large share of a catalog by
      count and a small share of it by revenue. Only the second number affects
      what the catalog is worth.</figcaption>
      <svg class="chart" viewBox="0 0 720 132" role="img"
           aria-label="Catalog composition by track count and by revenue">
        %s
        %s
        %s
        %s
      </svg>
      <div class="legend">%s</div>
    </figure>""" % (
        label % (12.0, "By track count &#8212; %d assets" % total_n),
        _bar([(k, n, n / total_n) for k, n in counts], 20.0),
        label % (88.0, "By annual revenue &#8212; $%s"
                 % f"{val.annual_revenue_usd:,.0f}"),
        rev_row, legend)


def _origin_section(assets) -> str:
    """Which generators the flagged material was attributed to.

    A buyer can act on "twelve tracks attributed to Suno" in a way they cannot
    act on "twelve tracks scored above 65". It names the thing.
    """
    tally = {}
    for a in assets:
        sc = a.score
        if not sc or not sc.ok or not sc.origin:
            continue
        row = tally.setdefault(sc.origin, {"n": 0, "usd": 0.0, "line": ""})
        row["n"] += 1
        row["usd"] += a.annual_usd
        if not row["line"] and sc.origin_summary:
            row["line"] = sc.origin_summary

    if not tally:
        return ""

    rows = "".join(
        "<tr><td>%s</td><td class='n'>%d</td><td class='n'>$%s</td>"
        "<td>%s</td></tr>"
        % (html.escape(name.title()), v["n"], f"{v['usd']:,.0f}",
           html.escape(v["line"][:110]))
        for name, v in sorted(tally.items(), key=lambda kv: -kv[1]["n"]))

    return """
    <h2>Attribution</h2>
    <p>HumanStandard places each recording against reference populations of
    verified human music and known generators. Where a track sits clearly
    inside one of those populations, the generator is named.</p>
    <div class="wrapt"><table>
      <thead><tr><th>Attributed to</th><th class="n">Tracks</th>
      <th class="n">Annual</th><th>Basis</th></tr></thead>
      <tbody>%s</tbody></table></div>
    <p class="note">Attribution is neighbourhood evidence, not the verdict
    itself. It tells a buyer which supplier the material resembles, which is
    what a seller will be asked about.</p>""" % rows


def _industry_label_section(assets) -> str:
    """Counts against the July 2026 IFPI/RIAA GenAI labeling standard."""
    meets = [a for a in assets if a.score and a.score.ok
             and a.score.industry_label_status == "meets_definition"]
    suspected = [a for a in assets if a.score and a.score.ok
                 and a.score.industry_label_status == "suspected"]
    if not meets and not suspected:
        return ""

    return """
    <h2>Industry labelling exposure</h2>
    <p>In July 2026 IFPI, RIAA, A2IM, WIN and IMPALA adopted a unified
    track-level standard distinguishing <strong>AI-Generated</strong> from
    AI-Assisted recordings. HumanStandard reports each verdict against those
    definitions, so this is the label the catalog would carry on a platform
    that adopts the standard &mdash; not a private score.</p>
    <div class="kv">
      <div>Meets the AI-Generated definition</div><div class="v">%d</div>
      <div>Revenue on those assets</div><div class="v">$%s</div>
      <div>Suspected, stem verification recommended</div><div class="v">%d</div>
      <div>Revenue on those assets</div><div class="v">$%s</div>
    </div>
    <p class="note">The labelling programme is voluntary and self-declared.
    A seller who has not declared these is not necessarily concealing them,
    but the buyer inherits the declaration either way.</p>""" % (
        len(meets), f"{sum(a.annual_usd for a in meets):,.0f}",
        len(suspected), f"{sum(a.annual_usd for a in suspected):,.0f}")


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
        origin = ""
        if a.score and a.score.ok and a.score.origin:
            origin = '<span class="tag">%s</span>' % e(a.score.origin.title())
        rows.append(
            "<tr><td>%s</td><td>%s</td><td class='n'>%s</td>"
            "<td class='n'>$%s</td><td>%s %s</td></tr>"
            % (e(title[:52]), e(artist[:28]), score,
               f"{a.annual_usd:,.0f}", _tier_pill(a.tier), origin)
        )
    more = ""
    if len(ranked) > 60:
        more = ("<p class='note'>Showing the 60 highest-earning assets of %d. "
                "Full detail in audit.json.</p>" % len(ranked))

    evidence_for = {
        a.filename: a.score.origin_map_evidence
        for a in result.assets
        if a.score and a.score.ok and a.score.origin_map_evidence
    }
    review_rows = []
    for item in result.review_queue[:40]:
        link = evidence_for.get(item["filename"], "")
        reason = e(item["reason"])
        if link:
            reason += (' <a class="ev" href="%s">similarity map</a>'
                       % e(link, quote=True))
        review_rows.append(
            "<tr><td>%s</td><td class='n'>%s</td><td class='n'>$%s</td>"
            "<td>%s</td></tr>"
            % (e(item["filename"][:52]),
               "%.0f" % item["ai_score"] if item["ai_score"] >= 0 else "&mdash;",
               f"{item['annual_usd']:,.0f}", reason)
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

<h2>Where the risk sits</h2>
%s

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
%s
%s

<h2>Method</h2>
<p>Every asset was submitted to the HumanStandard detection API, which returns
a three-valued verdict &mdash; AI, human, or uncertain &mdash; at three
calibrated operating points with their false-positive rates published.
Assets are tiered on those operating points rather than on thresholds chosen
here: %s. Where the operating points disagree, the recording is borderline by
the detector's own account and is routed to a person. Anything below the
detector's confidence floor is routed to review regardless of verdict, and
anything that could not be scored is excluded from the clean base rather than
assumed safe. Suspect revenue is escrowed in full; contested revenue is
weighted at %.0f%% because uncertainty is not a finding.</p>

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
        _exposure_chart(val),
        val.track_count, f"{val.annual_revenue_usd:,.0f}", val.multiple,
        f"{val.asking_price_usd:,.0f}",
        val.clean_count, val.contested_count, val.suspect_count, val.error_count,
        f"{val.suspect_revenue_usd:,.0f}", val.suspect_revenue_share * 100,
        f"{val.contested_revenue_usd:,.0f}", val.contested_revenue_share * 100,
        f"{val.recommended_escrow_usd:,.0f}",
        e(val.escrow_basis),
        "".join(rows), more, review_section, eval_section,
        _industry_label_section(result.assets),
        _origin_section(result.assets),
        e(config.thresholds_summary()), config.CONTESTED_ESCROW_WEIGHT * 100,
        generated, e(result.provider), e(result.manifest_sha256[:32]),
    )


def write(result: AuditResult, allow_mock: bool = False) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / "risk_memo.html"
    path.write_text(render(result, allow_mock=allow_mock), encoding="utf-8")
    return path
