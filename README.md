# Catalog Risk Auditor

[![tests](https://github.com/codergirl73/catalog-risk-auditor/actions/workflows/tests.yml/badge.svg)](https://github.com/codergirl73/catalog-risk-auditor/actions/workflows/tests.yml)
[![codeql](https://github.com/codergirl73/catalog-risk-auditor/actions/workflows/codeql.yml/badge.svg)](https://github.com/codergirl73/catalog-risk-auditor/actions/workflows/codeql.yml)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/codergirl73/catalog-risk-auditor/actions)
[![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](SECURITY.md)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**Audit a music catalog for AI-generated content before you buy it.**

### ▶ [Watch a real audit run](https://codergirl73.github.io/catalog-risk-auditor/) &nbsp;·&nbsp; [read the memo it produced](https://codergirl73.github.io/catalog-risk-auditor/memo.html)

A recording of an actual run against the live HumanStandard API — the agent's
plan, the events it emitted, and the numbers it computed. Nothing staged.

Music catalogs are bought and sold as income streams, priced at a multiple of
annual royalties. A buyer verifies who owns the songs and what they earn. Until
now nobody verified whether a person made them.

That matters for three reasons, and they compound:

- **Ownership.** Work generated without meaningful human authorship is not
  protectable under US copyright. Part of the catalog may not be ownable.
- **Monetisation.** Platforms are demonetising synthetic content. Deezer found
  that up to 85% of streams on fully AI-generated tracks were fraudulent in
  2025 and cut them from royalty payments.
- **Scale.** Deezer reported 90,000 AI-generated uploads per day — over half of
  all new uploads at peak in June 2026. Any recently assembled catalog is
  statistically likely to contain them, whether or not the seller knows.

This tool scores every asset through the HumanStandard detection API, sorts
them, joins the result to the seller's revenue, and produces the number a
buyer actually negotiates with: a recommended escrow.

## What it produces

- **An acquisition risk memo** (printable HTML) — exposure by count and by
  revenue, a recommended escrow with its basis stated, and the assets ranked by
  what they earn.
- **A human-review queue** — every asset the detector could not settle, with
  the reason it was escalated.
- **An accuracy report** — when the catalog carries ground-truth labels, the
  memo states precision, recall and the false positives by name.

## Install

Nothing to install. Python 3.10+ and the standard library.

```bash
cp .env.example .env      # add your HS_API_KEY
```

## Use

```bash
# 1. verify the integration for ZERO credits. Runs the full
#    submit -> poll -> map -> tier cycle against HumanStandard's own
#    ?mock= fixtures: real endpoint, real auth, real response shapes,
#    nothing billed.
python3 scripts/probe_api.py

# 2. spend exactly one credit on a real analysis and save the raw
#    response to out/api_evidence.json.
python3 scripts/probe_api.py --upload path/to/track.mp3

# 3. build a catalog: CC human music from Internet Archive netlabels
python3 scripts/fetch_catalog.py --limit 116

# 4. plant real AI tracks: Suno and Udio songs from the SONICS dataset.
#    Reads the 3.8 GB archive's index over HTTP byte ranges and pulls out
#    only the songs it needs -- about 30 MB on the wire.
python3 scripts/fetch_ai_tracks.py --limit 14

# 5. label and price the catalog
python3 scripts/build_dataset.py

# 6. audit
python3 run.py data/catalog \
    --royalties data/royalties.csv \
    --truth data/ground_truth.csv \
    --json
```

The memo lands in `out/risk_memo.html`. To score a single file and see the
raw response: `python3 -m catalog_audit.detector path/to/track.mp3`.

### Watching it work

Add `--serve` and the same audit runs in a browser instead of the terminal —
the plan with each step lighting up as the agent reaches it, the events as
they are emitted, the count-versus-revenue bars, and the memo one click away:

```bash
python3 run.py data/demo_catalog --serve \
    --royalties data/royalties.csv --truth data/ground_truth.csv
```

It is `http.server` and server-sent events, so it adds nothing to install. It
binds to `127.0.0.1` only, the catalog path comes from the command line rather
than any request, and there is no static file handler — the routes are a fixed
table, so there is nothing to traverse out of.

## How it decides

**The thresholds are not ours.** HumanStandard publishes three calibrated
operating points with their false-positive rates attached, and the agent tiers
on those rather than on numbers we picked:

| Operating point | False-positive rate | Its purpose |
|---|---|---|
| `press_safe` | ~0% | Certification, public attestation |
| `human_safe` | ~1–2% | Auto-reject at distribution |
| `recall` | ~5–10% | Manual-review net |

An acquisition is an auto-reject decision with money attached, so:

| Tier | Rule | Treatment |
|---|---|---|
| **Clean** | `recall` still says human | Counted in the acquirable base |
| **Suspect** | `human_safe` says ai | Escrowed in full |
| **Contested** | the operating points disagree | Escalated to human review; revenue weighted at 50% for escrow |

That middle row is the point. HumanStandard's own guidance is that a track
called AI at `recall` but human at `human_safe` is borderline — so the
contested band is *derived from the detector's own uncertainty* rather than
asserted by us.

Any verdict below the detector's confidence floor (default 0.60) is routed to
review regardless of what it said. Detection failures, oversized files and
assets left unscored when the budget ran out are all excluded from the clean
base rather than assumed safe.

A score-band fallback (clean under 25, suspect over 65) applies only to a
response without `tier_verdicts`. Every threshold is env-configurable and
printed on each run, because a stated threshold can be argued with and a
hidden one can only be trusted.

## What the API gives the memo

The detection call returns considerably more than a score, and the memo uses
all of it:

- **A three-valued verdict** — `ai`, `human`, or `uncertain`. The detector
  declines to call some tracks itself, and that refusal is carried to the
  buyer rather than rounded to the nearer answer.
- **Generator attribution** — `origin: "suno"`, with a plain-language basis:
  *"24 of its 25 nearest reference recordings are Suno generations."* A buyer
  can act on that in a way they cannot act on "scored 87".
- **An IFPI/RIAA industry label** — the July 2026 standard distinguishing
  AI-Generated from AI-Assisted. The memo reports the label the catalog would
  carry on a platform that adopts it, not a private score.
- **A per-window risk timeline** — so an escalation tells the reviewer *where
  to listen*, not merely that the agent was unsure.
- **A similarity-map image URL** — permanent, citable visual evidence, linked
  from each review-queue row.

## Design notes

**Zero dependencies.** Everything runs on the standard library — `urllib` for
HTTP, `csv` for data, `hashlib` for the audit trail. An empty dependency tree
has no supply chain to audit.

**Responses are cached by file hash.** Score a catalog once and re-run the
analysis as often as you like without spending another API call, or demo with
the network unplugged. The cache is also the agent's memory: it recognises a
track it has already scored, by content, across runs and across catalogs.

**Detection credits are budgeted, not trusted.** The agent hashes the catalog
up front, reports how many live calls it will need against `HS_CREDIT_BUDGET`,
and stops at the ceiling. Budget is decremented *before* each request, because
a request that times out may still have been charged. Anything left unscored
when the budget runs out is reported as unscored — **never as clean**. An asset
nobody paid to check is not an asset anybody verified.

**Every failure fails toward a human.** Unreachable API, unparseable response,
low confidence, oversized file, exhausted budget — each one routes the asset to
review rather than letting it pass as clean. See [SECURITY.md](SECURITY.md) for
the full table.

**The mock detector cannot produce a memo.** It exists so the pipeline can be
exercised before a key is in place. Its scores are derived from file hashes and
are meaningless; `memo.py` refuses to render a mock run unless explicitly
overridden, and marks the document if it does.

## Tests

187 tests, no framework to install:

```bash
python3 -m unittest discover -s tests -v
```

CI runs them on Python 3.10 through 3.13, and separately gates lint (`ruff`,
16 rule families), static security analysis (`bandit`), and cyclomatic
complexity (`radon` — any function scoring D or worse fails the build).
CodeQL's `security-and-quality` suite runs on every push and weekly. The
dependency tree is asserted empty on every push.

Current: zero lint findings, zero security findings, **90% coverage**, no
function above C complexity. GitHub Actions are pinned to commit SHAs and
watched by Dependabot; `sbom.json` records the empty runtime tree. See
[SECURITY.md](SECURITY.md).

## Disclosure

The demonstration catalog is constructed, and every part of it is traceable:

| Part | What it is |
|---|---|
| **Human tracks** | 116 real, CC-licensed recordings by real people, from [Internet Archive netlabel collections](https://archive.org/details/netlabels). Provenance in `data/human_manifest.csv`. |
| **AI tracks** | 14 real generated songs from the [SONICS dataset](https://huggingface.co/datasets/awsaf49/sonics) (Rahman et al., ICLR 2025), produced by **Suno** (v2/v3/v3.5) and **Udio** (v32/v130). CC BY-NC 4.0. Provenance in `data/ai_manifest.csv`. |
| **Royalty figures** | Generated. A Pareto distribution over the catalog, with AI tracks earning less per track by default — see `scripts/build_dataset.py`, which states the assumption and lets you remove it. |
| **Detection results** | **Real.** Every verdict comes from a live HumanStandard API call. `out/api_evidence.json` is a raw response. |
| **The acquisition** | Hypothetical. There is no Meridian Sound Library. |

No audio anywhere in this catalog was synthesised by us for the purpose of
being labelled "AI". The planted tracks are genuine generative-model output,
which is the only thing that makes the reported precision and recall mean
anything.

This tool produces an audio-authenticity assessment, not a legal opinion or a
valuation. Copyright enforceability of AI-generated works is a question for
counsel.

## License

MIT — see [LICENSE](LICENSE).
