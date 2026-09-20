# Security

## Dependency surface

There isn't one. The project imports only the Python standard library —
`urllib.request`, `csv`, `hashlib`, `json`, `wave`, `dataclasses`. There is no
`requirements.txt`, no `pyproject.toml`, no lockfile and no virtualenv step,
because there is nothing to install. CI asserts this on every push rather than
leaving it as a claim in a README.

An empty dependency tree cannot carry a transitive vulnerability, and there is
no install-time code execution to audit.

## Secrets

The HumanStandard API key is read from the environment, or from a `.env` file
that `.gitignore` excludes. It is never written to disk by the application,
never included in `audit.json`, and never rendered into the memo. CI fails the
build if a `.env` or `*.key` file is ever committed.

`out/api_evidence.json` records a real API *response* as required by the
submission. It contains no credential — the request record stores which auth
*style* succeeded, not the key itself.

## What the tool sends where

One thing leaves the machine: the audio files, uploaded to the HumanStandard
detection endpoint over HTTPS, one file per request. Nothing else is
transmitted. The royalty sheet, the ground-truth labels, the valuation and the
memo never leave local disk.

`scripts/fetch_catalog.py` additionally downloads public, CC-licensed audio
from Internet Archive. It sends no credentials.

## Spending guardrails

Detection credits are finite, so the agent is given an allowance rather than
being trusted to stop:

- `HS_CREDIT_BUDGET` caps live calls per run. The agent estimates its spend
  before the first call and warns if the catalog exceeds it.
- Budget is decremented *before* the request, not after, because a request
  that times out may still have been charged.
- `HS_MAX_UPLOAD_MB` refuses oversized files instead of burning a credit and a
  long timeout on a DJ set.
- Responses are cached by SHA-256 of the file, so re-analysis costs nothing.
- Retries are bounded, and 400/401/403/415 are not retried — a request the
  server has already rejected on its merits will be rejected again.

## Failure handling

The governing rule is that an unverified asset is never reported as clean.

| Failure | Behaviour |
|---|---|
| API unreachable, times out, or returns non-JSON | Asset marked `error`, excluded from the clean base, routed to human review |
| Response in an unrecognised schema | `map_response()` raises with the observed keys named, rather than inventing a score |
| Detector confidence below `MIN_CONFIDENCE` | Asset forced to `contested` regardless of its score |
| Call budget exhausted | Remaining assets marked `error`, explicitly *not* clean |
| File above the upload ceiling | Refused before the request, no credit spent |
| No API key present | Mock detector, loudly flagged, and the memo refuses to render |

## The mock detector

`MockDetector` derives scores from file hashes. They are meaningless. It exists
so the pipeline can be exercised before a key is in place.

It is fenced three ways: every event stream announces it, `AuditResult.mock_mode`
records it, and `memo.render()` raises `MockModeRefused` rather than producing a
document full of invented numbers. The `--allow-mock` override stamps a banner
across the top of the memo. Tests cover all four behaviours.

## Reporting

This is a hackathon project. Open an issue on the repository.
