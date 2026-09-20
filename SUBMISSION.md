# Submission notes

Three tracks, one project, one repository:
**HumanStandard** · **The Code Registry** · **Open Track / Bring Your Own Project**

Repository: https://github.com/codergirl73/catalog-risk-auditor

Placeholders marked `[ ]` are filled from the real run. Do not fill them from a
mock run — `memo.py` refuses to render one, and that refusal is the point.

---

## The pitch, in one paragraph

A buyer pays a multiple of annual royalties for a music catalog. Diligence
verifies ownership, disputes, uncleared samples and revenue stability. Nobody
verifies that a human made the recordings. With AI material exceeding half of
daily uploads on some platforms, a recently assembled catalog can contain
tracks that may carry no enforceable copyright and whose income a platform can
switch off by policy. This agent audits the catalog before the wire goes out
and returns the number the buyer negotiates with: a recommended escrow.

---

# Track 1 — HumanStandard

### Requirements checklist

- [x] Public code repository — https://github.com/codergirl73/catalog-risk-auditor
- [ ] Demo video, 3 minutes or less
- [x] Written problem description and target music industry user — below
- [x] Explanation of API integration and how verdicts are used — below
- [ ] Evidence of at least one real API call — `out/api_evidence.json`,
      produced by `scripts/probe_api.py --upload`
- [ ] All team members listed — solo
- [ ] **Opt in to the HumanStandard prize on the Devpost form** (a checkbox;
      it is not automatic)

### Problem and intended user

**User:** the catalog acquisition analyst at a music rights fund, publisher or
label — the person who runs diligence before the fund wires money. Secondary:
the distributor deciding whether to onboard a back catalogue.

**Problem:** catalogs are priced at a multiple of annual royalties. Diligence
covers chain of title, disputes, uncleared samples and revenue durability. It
does not cover whether a person made the recordings. That gap matters three
ways, and they compound:

1. **Ownership.** Work without meaningful human authorship is not protectable
   under US copyright. Part of the catalog may not be ownable at all.
2. **Monetisation.** Platforms are demonetising synthetic content. Income the
   seller reports as recurring can be switched off by a policy change.
3. **Revenue quality.** Where synthetic uploads are paired with stream fraud,
   the reported income was never real to begin with.

The buyer finds out after closing, when the price is already paid.

### How the HumanStandard API is integrated

Every asset is submitted to `POST /api/analyze` on
`https://app.jobsbyhumans.com` (`catalog_audit/detector.py`). The endpoint is
**asynchronous**: it returns a `job_id`, and the client polls
`/api/jobs/{job_id}/status` until the verdict lands, with bounded retries and a
documented 20–30s cold-start allowance. `?detail=full` is requested on every
call because `tier_verdicts` is what the agent tiers on. Responses are cached
on disk keyed by SHA-256 of the file, so a catalog is scored once and analysed
many times.

**The API result is not the output. It is one input to a decision:**

1. **The verdict is three-valued and stays that way.** `ai`, `human`,
   `uncertain`. The detector declines to call some tracks, and that refusal is
   carried to the buyer rather than rounded to the nearer answer.
2. **Tiering uses HumanStandard's own calibrated operating points, not
   thresholds we invented.** `press_safe` (~0% FPR), `human_safe` (~1–2%) and
   `recall` (~5–10%) are published with their false-positive rates attached.
   An acquisition is an auto-reject decision with money attached, so
   `human_safe` gates *suspect*, and anything the widest `recall` net still
   calls human is *clean*.
3. **The contested band is derived from the detector's own disagreement.**
   Where the three operating points split — AI at `recall`, human at
   `human_safe` — HumanStandard's guidance is that the track is borderline.
   That is exactly the escalation rule, so the uncertainty is theirs, measured,
   rather than ours, asserted. Confidence below `MIN_CONFIDENCE` forces review
   regardless.
4. **Tier decisions are joined to the seller's revenue sheet.** This is where
   a detection result becomes a price.
5. **The agent's own accuracy is measured and reported** against planted
   ground-truth labels, by name.

### What the memo does with the rest of the response

The API returns far more than a verdict, and a buyer can act on the rest:

- **`origin`** attributes a generator by name, with HumanStandard's own basis
  for saying so: *"24 of its 25 nearest reference recordings are Suno
  generations."* The memo has an Attribution section counting tracks and
  revenue per generator.
- **`industry_label`** maps onto the July 2026 IFPI/RIAA/A2IM/WIN/IMPALA GenAI
  labeling standard. The memo reports how much of the catalog meets the
  **AI-Generated** definition and how much is *suspected* pending stem
  verification — an industry-standard label, not a private score.
- **`risk_timeline`** gives per-window probability, so a review-queue entry
  says *where to listen* ("risk peaks at 94% around 2:14") instead of only
  that the agent was unsure.
- **`origin_map_evidence`** is a permanent similarity-map image URL, linked
  from each escalated row as citable evidence.

### Credits, and not wasting them

The key carries 200 credits and one credit is one scan, so spending is
guarded rather than trusted. `HS_CREDIT_BUDGET` caps live calls; the agent
hashes the catalog up front and reports what the run will cost *before*
spending anything; budget is decremented before each request because a
request that times out may still have been billed; and responses are cached by
file hash so re-analysis is free.

`scripts/probe_api.py` verifies the entire integration — submit, poll, map,
tier — against HumanStandard's `?mock=` fixtures for **zero credits** before
a single real call is made.

### The fixture that cannot become a finding

Mock responses come back over the real API, from a real key, with real field
shapes, and carry `"mock": true`. That flag is propagated onto every
`TrackScore` and promoted to the whole `AuditResult`, so a run built on
fixtures is marked mock and `memo.render()` refuses it — even though nothing
about the connection was fake. Tested.

### How verdicts are communicated

Honestly, and with the uncertainty left in:

- Three tiers, not two. The agent declines to call what it cannot settle.
- Every escalation carries its reason in plain language.
- Detection failures are excluded from the clean base, never assumed safe.
- Assets left unscored because the call budget ran out are reported as
  unscored — an asset nobody paid to check is not an asset anybody verified.
- A mock run cannot produce a memo. `memo.render()` raises `MockModeRefused`
  rather than putting invented numbers in front of a buyer.

---

# Track 2 — The Code Registry

Scored on Code Score across **security, dependencies and quality**.

- [x] Public repository (private repos cannot be analysed)
- [ ] Registered, project created, **repository sync started before 18:00**
- [ ] Team name, repo URL and Code Registry account email logged at the
      sponsor table before leaving
- [x] `python3 -m unittest discover -s tests` passes — 73 tests
- [x] `.env` never committed; CI fails the build if one appears

### Dependencies

The dependency tree is **empty**. No `requirements.txt`, no `pyproject.toml`,
no lockfile, no virtualenv. Only the standard library: `urllib.request`,
`csv`, `hashlib`, `json`, `dataclasses`, `argparse`.

This is not minimalism for its own sake — an empty dependency tree cannot
carry a transitive vulnerability and has no install-time code execution to
audit. **CI asserts it on every push** rather than leaving it as a README
claim.

### Security

Full detail in [SECURITY.md](SECURITY.md). Summary:

- API key read from env or a gitignored `.env`; never written to disk, never
  in `audit.json`, never in the memo. The evidence file records which auth
  *style* worked, not the key.
- One thing leaves the machine: audio, over HTTPS, to the detection endpoint.
  Royalties, labels, valuation and memo stay local.
- Memo output is HTML-escaped throughout, with a test that feeds it hostile
  filenames and asserts no script survives.
- Bounded retries; 400/401/403/415 are not retried.
- Spending guardrails: credit budget, upload ceiling, response caching.

### Quality

- 73 unit and integration tests, no test framework required — `unittest` only.
- CI on Python 3.10, 3.11, 3.12 and 3.13.
- Docstrings throughout, explaining *why* rather than restating the code.
- Small modules with one job each; no module over ~250 lines.
- MIT licensed.

---

# Track 3 — Open Track / Bring Your Own Project

Judged on technical execution, agentic design, innovation, impact, reliability
and safety, and demo completeness. The same project, framed on the agent loop
rather than the music.

### Agentic design

The agent **states a plan, then executes exactly that plan** — there is a test
asserting the executed steps equal the declared ones, so the plan cannot drift
from the work.

- **Planning:** seven declared steps, emitted before any work begins.
- **Tool calling:** an external detection API, a revenue sheet, a label file.
- **Memory:** content-addressed response cache. The agent recognises a track
  it has already scored, by hash, across runs and across catalogs.
- **Budgeted action:** it estimates its spend before spending, and stops at
  the ceiling.
- **Escalation:** what it cannot settle goes to a human with a reason,
  rather than being forced into a verdict.
- **Self-evaluation:** it scores its own output against ground truth and
  publishes its error rate in the same document as its finding.

### Reliability and safety

| Failure | Behaviour |
|---|---|
| API unreachable / times out / non-JSON | Asset marked `error`, excluded from the clean base, routed to review |
| Unrecognised response schema | Raises with the observed keys named, rather than inventing a score |
| Confidence below the floor | Forced to `contested` regardless of score |
| Call budget exhausted | Remaining assets marked unscored, explicitly **not** clean |
| File above the upload ceiling | Refused before the request; no credit spent |
| No API key | Mock detector, loudly flagged, and the memo refuses to render |

The governing rule, and the one worth saying out loud in the demo: **an
unverified asset is never reported as clean.** Every failure mode fails toward
"a human should look at this", never toward "probably fine".

### Innovation

Everyone else's AI-detection demo outputs a score. This outputs a dollar
figure, an escrow recommendation, and its own error rate.

---

## Results from the demo run

Fill from `out/risk_memo.html` and `out/audit.json` after a real run.

- Catalog size: `[ ]` tracks (116 human, `[ ]` AI)
- API calls spent: `[ ]` of 200 credits
- Reported annual revenue: `[ ]`
- Clean / contested / suspect: `[ ]` / `[ ]` / `[ ]`
- Suspect share **by count**: `[ ]`% — **by revenue**: `[ ]`%
- Recommended escrow: `[ ]` against an asking price of `[ ]`
- Precision / recall against planted labels: `[ ]` / `[ ]`
- Human tracks wrongly flagged: `[ ]`
- Assets routed to human review: `[ ]`

> **The line worth saying out loud:** suspect tracks are `[ ]`% of the catalog
> by count but only `[ ]`% of its revenue. Synthetic uploads accumulate far
> faster than they earn — which is why a count-based audit misprices the deal
> in both directions.

---

## Tools, models, frameworks and runtimes

| Component | Choice |
|---|---|
| Language / runtime | Python 3.10+ (CI: 3.10, 3.11, 3.12, 3.13) |
| Dependencies | None. Standard library only. |
| Detection model | HumanStandard detection API |
| HTTP | `urllib.request` |
| Data | `csv`, `dataclasses` |
| Audit trail | `hashlib` SHA-256 |
| Output | Hand-written HTML, no template engine |
| Tests | `unittest`, 73 tests |
| CI | GitHub Actions |

---

## Setup and testing instructions

```bash
git clone https://github.com/codergirl73/catalog-risk-auditor
cd catalog-risk-auditor
python3 -m unittest discover -s tests     # 73 tests, nothing to install

cp .env.example .env                      # add HS_API_KEY

python3 scripts/probe_api.py              # find the endpoint, spends nothing
python3 scripts/probe_api.py --upload track.mp3   # one call, saves evidence

python3 scripts/fetch_catalog.py --limit 116      # CC human music
# add AI-generated tracks to data/catalog/ai/
python3 scripts/build_dataset.py                  # labels + royalty sheet

python3 run.py data/catalog \
    --royalties data/royalties.csv \
    --truth data/ground_truth.csv \
    --json
```

Memo lands in `out/risk_memo.html`.

---

## Disclosure

The catalog and its royalty figures are constructed for demonstration. The
audio is real, CC-licensed, human-made music from Internet Archive netlabel
collections; the AI tracks were generated deliberately and labelled so that
accuracy could be measured; **the HumanStandard API responses are real**; the
acquisition scenario is hypothetical.

This tool produces an audio-authenticity assessment, not a legal opinion or a
valuation. Copyright enforceability of AI-generated works is a question for
counsel.

---

## Demo script, 3 minutes

| Time | Beat |
|---|---|
| 0:00–0:20 | The setup. "A fund is about to pay $`[ ]` for this catalog. Diligence checked who owns it and what it earns. Nobody checked whether a person made it." |
| 0:20–0:40 | Show the folder. State the thresholds on screen — they print on every run. |
| 0:40–1:40 | The agent runs. Plan first. Then the budget estimate: "115 calls needed, 200 credits." Then scoring, tiering, escalation. |
| 1:40–2:20 | The memo. Lead with the escrow figure. Then the count-vs-revenue line — this is the moment. |
| 2:20–2:45 | Accuracy. "I planted `[ ]` AI tracks. It caught `[ ]`, missed `[ ]`, wrongly flagged `[ ]` humans." |
| 2:45–3:00 | Close on the contested band. "`[ ]` tracks I couldn't call from audio. I'm not guessing on a $`[ ]` decision — those go to a person, and here's the list with reasons." |

---

## If you fall behind, cut in this order

1. `--json` output and anything reading `audit.json`
2. Print styling on the memo
3. The review-queue table in the memo (keep the count)
4. Colour in the terminal output

**Never cut:** one real API call in evidence, the three tiers, the revenue
join, the human-review list.
