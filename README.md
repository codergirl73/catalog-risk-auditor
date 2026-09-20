# Catalog Risk Auditor

**Audit a music catalog for AI-generated content before you buy it.**

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
# 1. verify the API and capture a real response
python3 -m catalog_audit.detector path/to/track.mp3

# 2. build a catalog: CC human music from Internet Archive netlabels
python3 scripts/fetch_catalog.py --limit 150

# 3. add AI-generated tracks to data/catalog/ai/, then label and price it
python3 scripts/build_dataset.py

# 4. audit
python3 run.py data/catalog \
    --royalties data/royalties.csv \
    --truth data/ground_truth.csv \
    --json
```

The memo lands in `out/risk_memo.html`.

## How it decides

| Tier | Score | Treatment |
|---|---|---|
| Clean | below 25 | Counted in the acquirable base |
| Contested | 25–65 | Escalated to human review; revenue weighted at 50% for escrow |
| Suspect | above 65 | Escrowed in full |

Any result below the detector's own confidence floor (default 0.60) is routed
to human review regardless of its score. Detection failures are excluded from
the clean base rather than assumed safe.

Thresholds are env-configurable and printed on every run, because a stated
threshold can be argued with and a hidden one can only be trusted.

## Design notes

**Zero dependencies.** Everything runs on the standard library — `urllib` for
HTTP, `csv` for data, `hashlib` for the audit trail. An empty dependency tree
has no supply chain to audit.

**Responses are cached by file hash.** Score a catalog once and re-run the
analysis as often as you like without spending another API call, or demo with
the network unplugged.

**The mock detector cannot produce a memo.** It exists so the pipeline can be
exercised before a key is in place. Its scores are derived from file hashes and
are meaningless; `memo.py` refuses to render a mock run unless explicitly
overridden, and marks the document if it does.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Disclosure

The demonstration catalog and its royalty figures are constructed. The audio is
real, CC-licensed, human-made music; the AI tracks were generated deliberately
and labelled so accuracy could be measured; the API responses are real; the
acquisition scenario is hypothetical.

This tool produces an audio-authenticity assessment, not a legal opinion or a
valuation. Copyright enforceability of AI-generated works is a question for
counsel.

## License

MIT — see [LICENSE](LICENSE).
