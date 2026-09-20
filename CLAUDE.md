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

Working and tested end to end against synthetic audio. 22 unit tests pass.

```
catalog_audit/
  config.py      env-driven thresholds, all printable
  models.py      dataclasses: TrackScore, Asset, Valuation, Evaluation, ...
  detector.py    HumanStandard client + cache + dev-only mock
  tiering.py     three-tier classification, low-confidence override
  valuation.py   revenue join, exposure, escrow maths
  evaluation.py  confusion matrix vs planted labels
  memo.py        printable HTML acquisition risk memo
  agent.py       the audit loop, yields Events
  cli.py         terminal entrypoint
scripts/
  fetch_catalog.py   Internet Archive netlabel downloader (stdlib urllib)
  build_dataset.py   ground truth + synthetic royalty sheet
tests/               unittest, no framework needed
```

## Hard constraints — do not break these

- **Zero runtime dependencies. Standard library only.** This is deliberate: the
  Code Registry track scores dependencies and security, and an empty dependency
  tree cannot have a vulnerability. Do not add `requests`, `pandas`, `numpy`,
  `fastapi` or anything else. `urllib.request`, `csv`, `wave`, `hashlib` and
  `dataclasses` cover everything needed.
- **Python 3.10** — that is what is on the Mac. No `match` on dict patterns,
  no `tomllib`, no `typing.Self`.
- **Never commit `.env` or any API key.** `.gitignore` covers it; check before
  every push.
- **Never fabricate detection results.** The mock detector exists only so the
  pipeline can be exercised before a key is in place. It is marked mock
  throughout, and `memo.py` raises `MockModeRefused` rather than rendering a
  document full of invented numbers. Keep that gate. If a real run produces a
  less dramatic result than hoped, the real result is what ships.

## What is NOT done yet

1. **The live API has never been called.** `detector.map_response()` and
   `LiveDetector._build_request()` are defensive guesses — HumanStandard
   publishes no open API docs. First job of the day:
   ```
   python3 -m catalog_audit.detector path/to/one/track.mp3
   ```
   Screenshot the raw response (that satisfies Devpost requirement 5), then fix
   those two functions against the real schema. Nothing downstream should need
   to change.
2. **No real catalog downloaded.** Run `scripts/fetch_catalog.py`, then drop
   self-generated AI tracks into `data/catalog/ai/`, then
   `scripts/build_dataset.py`.
3. **`SUBMISSION.md` is drafted but has placeholders** for the real numbers.
4. **No repo pushed yet.**

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
