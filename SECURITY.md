# Security

## Dependency surface

**At runtime, there isn't one.** The project imports only the Python standard
library — 26 modules, all of them stdlib. There is no `requirements.txt`, no
`pyproject.toml`, no lockfile and no virtualenv step, because there is nothing
to install. CI asserts this on every push, and `sbom.json` (CycloneDX 1.5)
records it as a signed-off artefact with zero components.

An empty runtime tree cannot carry a transitive vulnerability, and there is no
install-time code execution to audit.

### CI is not exempt

A workflow's `uses:` entries are third-party code running with access to the
repository, so they are dependencies whether or not they appear in a manifest.

- Every action is **pinned to a full commit SHA**, not a tag. A tag is mutable:
  whoever controls it can repoint `@v4` at different code, and nothing in the
  repository would change.
- The human-readable version sits in a comment beside each pin, and
  **Dependabot** watches them weekly so those comments stay honest and the
  pins stay current.
- Analysis tooling (`ruff`, `bandit`, `radon`, `coverage`) installs in the CI
  job only. None of it is importable by the shipped package, and the test job
  proves that separately on four Python versions.

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

`scripts/fetch_catalog.py` and `scripts/fetch_ai_tracks.py` additionally
download public, openly licensed audio from Internet Archive and HuggingFace.
Neither sends a credential.

## One place opens a socket

`urllib.request.urlopen` is not a web client. It is a URL opener, and left
unchecked it will open `file:///etc/passwd` and return the contents as though
a server had sent them. Every URL this project opens is assembled from
configuration — `HS_API_BASE` out of a `.env`, a dataset host, an archive
identifier — and none of those is a boundary we control.

`catalog_audit/net.py` is therefore the only module permitted to open a
socket. It pins the scheme to **https** and refuses anything else, including
plain `http`, rather than silently upgrading it: a configuration asking for
`http` is a mistake somebody should be told about.

`tests/test_net.py` walks the source of every module and fails if any of them
calls `urllib.request.urlopen` directly. The guarantee is structural, not a
convention.

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

## The memo is a document someone else opens

The memo embeds values that arrived from a detection API — a generator name, a
plain-language basis, an evidence URL — and is then opened in a browser by the
person least equipped to notice if one of them is hostile.

- **Every interpolated value is HTML-escaped**, with tests that feed it
  hostile filenames and reasons.
- **Evidence URLs are checked for scheme before they become links.** Escaping
  prevents an attacker closing the attribute; it does nothing about the
  scheme, so `javascript:alert(1)` survives `html.escape` completely intact
  and becomes a live link. Only `https:` is linked; anything else is dropped
  and the row still renders. This was a real defect, found by writing the
  attack rather than by any scanner — bandit, ruff and CodeQL all pass over it
  because the sink is a hand-built string.
- **A Content Security Policy** forbids the lot regardless:
  `default-src 'none'`, no scripts at all, styles inline only,
  `form-action 'none'`, `base-uri 'none'`.
- External links carry `rel="noopener noreferrer nofollow"` and the document
  sets `referrer: no-referrer`, so opening one leaks nothing about where the
  memo lives on disk.
- The memo loads nothing over the network. Tested.

## The catalog is assembled by the counterparty

That is the premise of the tool: somebody sends you a folder and you audit it
before wiring money. So the folder is untrusted input.

A symlink is the one way a file can appear to be in the catalog while actually
being elsewhere on the machine. `track02.mp3` pointing at `~/.ssh/id_rsa`
would be uploaded to a third-party API for analysis. Files that resolve
outside the catalog directory are therefore **not scored**, and the refusal is
announced with the filenames rather than dropped silently.
`FOLLOW_EXTERNAL_SYMLINKS=1` lifts it for a catalog you assembled yourself.

## Redirects are not a hole in the scheme pinning

Checking only the first URL pins nothing. `urllib` permits redirects to
`http`, `https` and `ftp`, and copies the request headers onto the new
request — so an HTTPS endpoint answering `302 http://attacker/` would have
sent `Authorization: Bearer <key>` in cleartext.

- **Every redirect target is re-checked** against the same https-only rule.
- **Credential headers are stripped when the host changes**, so a redirect to
  a different HTTPS host cannot walk off with the bearer token. That is the
  same class of bug as CVE-2018-20060.
- Same-host redirects keep their headers, so ordinary API behaviour works.

## The key never appears in output

Error bodies are quoted back to help whoever is debugging, and the same text
reaches the response cache and `audit.json`. A misconfigured server that
echoes request headers would put the key in one, so anything surfaced is
passed through `config.redact()` first.

## Nothing reads an unbounded response

A `Content-Length` header is a claim, not a constraint, and reading a body
whole lets the far end decide how much memory this process allocates.

- API and metadata responses are read through a capped reader
  (`net.read_capped`) that **refuses** rather than truncating — a truncated
  payload fails as a JSON parse error and sends whoever is debugging after a
  schema problem that does not exist.
- ZIP members are checked against the size their own header declares before
  being inflated, and the read is bounded one byte past it, so a small archive
  cannot become a large allocation.
- Uploads are refused above `HS_MAX_UPLOAD_MB`.
- Downloads stream in fixed chunks rather than accumulating.

## The local web UI

`--serve` starts an `http.server` on the developer's machine. What it refuses
to do matters as much as what it renders:

- **Binds to `127.0.0.1` only.** A pane showing somebody's catalog and its
  valuation is not a page to put on a LAN by accident.
- **The catalog path comes from the command line, never from a request.** No
  URL reaches the filesystem, because no URL names a file.
- **No static file handler.** Routes are an explicit table of five and the
  page, stylesheet and script are served from constants, so there is nothing
  to traverse out of. Tested against `..`, encoded `..` and `/.env`.
- **The same CSP as the memo**, plus `nosniff` and `no-referrer` on every
  response.
- **One audit at a time**, so two runs cannot race on the cache or the credit
  budget.
- Anything surfaced from a failure passes through `config.redact()` first.

## Known trust boundaries, stated plainly

These are accepted rather than solved, and worth knowing:

| Boundary | Position |
|---|---|
| `.cache/*.json` | Trusted. Anyone who can write there controls the verdicts. It holds API responses keyed by file hash; treat it as you would any local state directory. |
| `HS_API_BASE` | Trusted — it comes from your own `.env`. The scheme is pinned to https, but pointing it at an internal address is not prevented, because that is a configuration decision, not an attack. |
| `out/audit.json` | Contains full raw API responses, including evidence URLs. Gitignored. Review before sharing. |
| Royalty and label CSVs | Trusted; they are the buyer's own diligence files. |
| Uploaded audio | Leaves the machine. That is the function of the tool, and the only thing that does. |

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

## What is enforced, and where

A standard nobody checks lasts about a week, so each of these fails the build
rather than living in a document:

| Check | Tool | Gate |
|---|---|---|
| Runtime dependency tree is empty | CI shell | No `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` or `poetry.lock` may exist |
| Lint, 16 rule families incl. security | `ruff` | Any finding fails |
| Static security analysis | `bandit` | Any finding fails |
| Cyclomatic complexity | `radon` | Any function scoring D or worse fails |
| Independent recurring scan | **CodeQL** | `security-and-quality` suite, every push and weekly |
| Secrets | CI shell | No `.env` or `*.key` tracked; the committed evidence file must stay credential-free |
| Behaviour | `unittest` | 187 tests, Python 3.10 / 3.11 / 3.12 / 3.13 |
| Test coverage | `coverage` | Fails below 85%; currently **90%** |
| SBOM accuracy | CI shell | `sbom.json` must still declare zero runtime components |
| CI supply chain | Dependabot | Actions pinned to SHAs, reviewed weekly |

Analysis tooling is installed in CI only. Nothing it checks is imported at
runtime, and the test job proves that separately on all four versions.

Current state: **zero lint findings, zero bandit findings, 90% test coverage,
no function above C complexity, average A.**

## Reporting

This is a hackathon project. Open an issue on the repository.
