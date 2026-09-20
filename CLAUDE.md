# Project context for Claude Code

Read this first. It is the handoff from the session that scaffolded this repo.

## What this is

**Catalog Risk Auditor** — an agent that audits a music catalog before someone
acquires it. It scores every track through the HumanStandard AI-detection API,
sorts assets into clean / contested / suspect, joins the result to the seller's
reported revenue, and outputs an acquisition risk memo with a recommended
escrow figure and a human-review queue.

Built for the Coffee & Code AI Agent Hackathon, Philadelphia, 20 Sep 2026.
Building window 10:00–18:00, hard stop 17:30. **Solo developer.**

Submitting to: **HumanStandard** (primary), **The Code Registry**, and
**Open Track / Bring Your Own Project**.

## The one-line pitch

A buyer pays a multiple of annual royalties for a catalog, verifies ownership
and revenue, and has no way to check whether the recordings are synthetic — so
they can pay millions for tracks that may carry no enforceable copyright and
whose income a platform can switch off by policy.

## The three things that must survive any cut

1. **The revenue join.** Everyone else outputs a score; this outputs a dollar
   figure. Suspect tracks are typically a large share by count and a small
   share by revenue — synthetic uploads accumulate far faster than they earn.
   Saying that out loud is the demo's best moment.
2. **The contested band.** The agent refuses to call tracks it cannot settle
   from audio and routes them to a person with a reason. "Verdicts
   communicated with appropriate uncertainty" is an explicit judging criterion.
3. **Ground-truth accuracy.** The catalog is constructed with known labels, so
   the memo reports precision, recall and false positives *by name*. Nobody
   else will show an error rate.

## Current state

Working end to end. **100 unit and integration tests pass.** CI runs them on
Python 3.10-3.13 on every push and asserts the dependency tree is still empty.

Repo: https://github.com/codergirl73/catalog-risk-auditor (public)

**The API is integrated against the real published schema** at
docs.hsverify.com, not against guesses. See "What the API actually does" below.

```
catalog_audit/
  config.py      env-driven thresholds, all printable
  models.py      dataclasses: TrackScore, Asset, Valuation, Evaluation, ...
  detector.py    HumanStandard client + cache + dev-only mock
  tiering.py     three-tier classification, low-confidence override
  valuation.py   revenue join, exposure, escrow maths
  evaluation.py  confusion matrix vs planted labels
  memo.py        printable HTML memo, inline-SVG exposure chart
  agent.py       the audit loop, yields Events
  cli.py         terminal entrypoint
scripts/
  probe_api.py       zero-credit integration verifier + evidence capture
  fetch_catalog.py   Internet Archive netlabel downloader (stdlib urllib)
  build_dataset.py   ground truth + synthetic royalty sheet
tests/               unittest, no framework needed
.github/workflows/   CI: tests on 3.10-3.13, asserts zero dependencies
```

## What the API actually does

Verified against docs.hsverify.com. Do not re-guess any of this.

- Base is **`https://app.jobsbyhumans.com`**. `api.hsverify.com` does not
  resolve.
- `POST /api/analyze` is **asynchronous**. It returns `{"job_id": ...}`; poll
  `GET /api/jobs/{job_id}/status` until `status == "complete"`, then read
  `result`. Cold starts 20-30s.
- Auth is `Authorization: Bearer <key>`. Upload is multipart `file=`, or JSON
  `{"url": ...}`. Sending both is a 422.
- `?detail=full` adds `risk_segments` and **`tier_verdicts`**, which is what
  tiering.py uses.
- `verdict` is three-valued: `ai` | `human` | `uncertain`.
- `tier_verdicts` gives three calibrated operating points with published FPRs:
  `press_safe` (~0%), `human_safe` (~1-2%), `recall` (~5-10%). **Tier on
  these, not on invented thresholds.** Disagreement between them is the
  contested band.
- Also returned: `origin` (generator name) + `origin_map.summary_line`,
  `industry_label` (IFPI/RIAA July 2026 standard), `risk_timeline`,
  `origin_map_evidence` (permanent image URL).
- **`?mock=human|ai|suspicious|no_vocal`** returns real-shaped fixtures and
  bills nothing. Responses carry `"mock": true`, which is propagated onto
  TrackScore and promoted to AuditResult so the memo refuses them. Use this
  for all development.
- 200 credits, one credit per scan. Rate limit 60/min.

## Hard constraints — do not break these

- **Zero runtime dependencies. Standard library only.** This is deliberate: the
  Code Registry track scores dependencies and security, and an empty dependency
  tree cannot have a vulnerability. Do not add `requests`, `pandas`, `numpy`,
  `fastapi` or anything else. `urllib.request`, `csv`, `wave`, `hashlib` and
  `dataclasses` cover everything needed.
- **Python 3.10 compatible.** The Mac actually runs 3.14 via Homebrew, and
  `/usr/bin/python3` is 3.9.6 — so do not rely on either being what CI tests.
  CI covers 3.10 through 3.13. No `match` on dict patterns, no `tomllib`,
  no `typing.Self`.
- **Never commit `.env` or any API key.** `.gitignore` covers it; check before
  every push.
- **Never fabricate detection results.** The mock detector exists only so the
  pipeline can be exercised before a key is in place. It is marked mock
  throughout, and `memo.py` raises `MockModeRefused` rather than rendering a
  document full of invented numbers. Keep that gate. If a real run produces a
  less dramatic result than hoped, the real result is what ships.

## What is NOT done yet

1. **The live API has never been called.** Everything is written against the
   published schema and verified against their `?mock=` fixtures, but no real
   credit has been spent. With the key in `.env`:
   ```
   python3 scripts/probe_api.py                      # zero credits
   python3 scripts/probe_api.py --upload <track.mp3> # one credit + evidence
   ```
   The second writes `out/api_evidence.json`, which satisfies the Devpost
   "evidence of at least one real API call" requirement.
2. **No AI tracks.** 116 human CC tracks are downloaded to
   `data/catalog/human/`. Generate AI tracks into `data/catalog/ai/`, then
   re-run `scripts/build_dataset.py` — it warns if the AI set is empty or thin.
3. **`SUBMISSION.md` has placeholders** for the real numbers.
4. **No demo video.**
5. **Code Registry sync not started.**

## Working agreement

- Small commits, run `python3 -m unittest discover -s tests` before each.
- Prefer editing existing modules over adding new ones; the structure is
  deliberately small.
- Time is the binding constraint, not elegance. If something is taking more
  than 30 minutes, cut it — the cut order is in `SUBMISSION.md`.

## Disclosure that must appear in the submission

The catalog and its royalty figures are constructed for demonstration. The
audio is real, CC-licensed, human-made music; the AI tracks were generated
deliberately and labelled; the API responses are real; the deal is
hypothetical. State this plainly — it costs nothing and removes the only
question a judge could raise about the numbers.
