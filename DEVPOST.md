**[▶ Step through a real audit](https://codergirl73.github.io/catalog-risk-auditor/)** · **[read the memo it produced](https://codergirl73.github.io/catalog-risk-auditor/memo.html)** · **[source](https://github.com/codergirl73/catalog-risk-auditor)**

---

## Inspiration

Music catalogs are bought and sold as income streams. A buyer pays a multiple
of annual royalties — commonly 10–20× — and diligence checks the things that
have always mattered: who owns the songs, whether there are disputes, whether
samples were cleared, whether the revenue is durable.

Nobody checks whether a person made the recordings.

That gap stopped being theoretical some time ago. AI-generated material now
arrives at a scale where any recently assembled catalog is statistically
likely to contain some, whether or not the seller knows. And it matters three
ways that compound:

1. **Ownership.** Work produced without meaningful human authorship isn't
   protectable under US copyright. Part of the catalog may not be ownable.
2. **Monetisation.** Platforms are demonetising synthetic content. Income a
   seller reports as recurring can be switched off by a policy change.
3. **Revenue quality.** Where synthetic uploads are paired with stream fraud,
   the reported income was never real.

The buyer finds out after closing, when the money is already wired.

We wanted to build the thing a diligence analyst would actually open — not a
classifier demo. A classifier outputs a score. An analyst needs a number they
can take into a negotiation.

## What it does

**Catalog Risk Auditor** audits a music catalog before acquisition. It scores
every asset through the HumanStandard detection API, sorts the catalog into
**clean / contested / suspect**, joins the result to the seller's reported
revenue, and produces an acquisition risk memo with a recommended escrow
figure and a human-review queue.

The escrow is the output that matters:

$$E = \left(R_{\text{suspect}} + w \cdot R_{\text{contested}}\right) \times m$$

Suspect revenue is held back in full. Contested revenue is weighted at
$w = 0.5$, because uncertainty is not a finding. Both are capitalised at the
acquisition multiple $m$, because the buyer is paying for a multiple, not a
year.

### The finding worth leading with

On the demonstration catalog, suspect assets are **37.5% of the catalog by
count but 17.4% of its revenue.**

That gap is the whole argument. Synthetic uploads accumulate far faster than
they earn, so a count-based read overstates the damage by more than a factor
of two. A seller saying *"only a few of my tracks are affected"* and a buyer
saying *"a third of this catalog is synthetic"* can both be telling the truth
about different numbers. Only one of them changes the price, and it is the one
nobody was computing.

Against a $720,000 asking price, that produces a **recommended escrow of
$171,710** — with every asset that contributed to it listed by name.

## How we built it

**Python 3.10+, standard library only. Zero runtime dependencies.** No
`requirements.txt`, no lockfile, no virtualenv — `urllib.request` for HTTP,
`csv` for data, `hashlib` for the audit trail, `dataclasses` throughout. CI
asserts the dependency tree is still empty on every push rather than leaving
it as a README claim, and `sbom.json` records it as a CycloneDX artefact with
zero components.

Eleven small modules, each with one job: config, models, detector, net,
tiering, valuation, evaluation, memo, agent, CLI, and a local web UI.

**The agent states a plan and then executes exactly that plan.** A test
asserts the executed steps equal the declared ones, so the plan cannot drift
from the work. It estimates its spend before spending, scores, tiers,
escalates what it cannot settle, joins to revenue, and finally grades itself.

**We tier on HumanStandard's calibrated operating points, not on thresholds we
invented.** `POST /api/analyze` with `?detail=full` returns three verdicts,
each published with its false-positive rate. `human_safe` saying *ai* makes an
asset suspect; `recall` still saying *human* makes it clean; disagreement
between them makes it contested and sends it to a person. The uncertainty is
the detector's, measured — not ours, asserted.

**Building the ground truth honestly was the part we cared most about.** The
human half is 116 real CC-licensed recordings from Internet Archive netlabel
collections. The AI half is 14 real Suno and Udio generations from the SONICS
dataset (Rahman et al., ICLR 2025, CC BY-NC 4.0).

We deliberately did *not* synthesise audio and label it "AI". It would have
been faster, and it would have made precision and recall meaningless — and the
error rate is the one number we most wanted to be able to state. Because
SONICS encodes the generating model in each filename, the answer key carries
not just *is this AI* but *which model made it*, so the audit grades
**attribution** as well as detection.

## Challenges we ran into

**The API is asynchronous, and we had assumed it wasn't.** `POST /api/analyze`
returns a `job_id`; the verdict arrives by polling `/api/jobs/{job_id}/status`.
Our first client expected the verdict in the POST response and would have
failed on every single call.

**Cloudflare, not authentication.** The first live request came back `403`
with `error code: 1010`. That reads exactly like a rejected API key and we
nearly went looking for one. It is a Cloudflare block on `urllib`'s default
`Python-urllib/3.x` user agent. Setting a real User-Agent fixed it.

**The live API differs from its own documentation** in four ways we only found
by reading real responses:

- `ai_probability` is on every response and is a direct 0–1 likelihood — a
  better score than reconstructing one from verdict plus confidence.
- `verdict` has a fourth value, `"suspicious"`, which the docs don't list. It
  means the screening threshold cleared and certification didn't — the
  contested band, stated in their vocabulary instead of ours.
- The timeline arrives as `risk_segments_full_mix` with real start and end
  seconds, not the documented flat `risk_timeline`.
- `industry_label_basis` — an array of plain-language reasons, absent from the
  response table entirely — turned out to be the best short evidence line the
  API produces.

We found all of them using **HumanStandard's `?mock=` mode**, which returns
real-shaped fixtures and bills nothing. The full submit→poll→map→tier cycle
was verified for zero credits before we spent one.

**200 credits, one per track.** Detection credits are finite and a catalog is
arbitrarily large, so the agent gets an allowance rather than being trusted to
stop. Budget is decremented *before* each request, because a request that
times out may still have been charged. Anything left unscored when the budget
runs out is reported as unscored — **never as clean**. An asset nobody paid to
check is not an asset anybody verified.

**Planting real AI tracks without downloading 3.8 GB.** The SONICS archive is
3.8 GB and we needed a dozen songs. HuggingFace serves byte ranges, and a ZIP
keeps its index at the end of the file, so we put a small seekable file-like
object over HTTP ranges and handed it to the standard library's `zipfile`,
which read the central directory and inflated only the members we asked for.
About 30 MB crossed the wire. Still zero dependencies.

**A palette that failed the people it was for.** The risk scale was
green/amber/red. Run through a colour-vision validator it scores ΔE 0.5
between *contested* and *suspect* under deuteranopia — a red-green colourblind
reader cannot tell two of the three tiers apart, in a document whose only job
is ranking risk. It is now blue/amber/red, which separates under every CVD
type, with every segment labelled so colour is never the sole signal.

## Accomplishments that we're proud of

**One track justified the entire design.**

`sonics__fake_00524_udio_1.mp3` is a real Udio generation. HumanStandard's
top-level verdict on it is **`"human"`**, at 29% confidence, and its similarity
map reports that *24 of its 25 nearest reference recordings are verified human
recordings.* Read the obvious field and you wave it straight into the clean
base.

But the calibrated operating points say otherwise:

| Operating point | FPR | Verdict on this track |
|---|---|---|
| `press_safe` | ~0% | human |
| `human_safe` | ~1–2% | **ai** |
| `recall` | ~5–10% | **ai** |

Because we tier on those, **it was caught and escrowed** — and the memo states
the disagreement on the asset's own row rather than quietly preferring one
number. A buyer told an asset is suspect deserves to know the top-line verdict
said otherwise, and why the calibrated number is the one to act on.

Everything needed to catch it was already in HumanStandard's response.

**The agent reports its own error rate.** Against the planted labels it caught
6 of 6, missed none, and wrongly flagged zero human tracks — and the memo says
in as many words that six is a sample rather than a measurement.

**It refuses to guess, in public.** Two tracks carrying $6,165 between them
went to the review queue with the split verdict, a timestamp for where to
listen, and a link to the similarity map.

**Quality that is measured rather than asserted:** 275 tests, 90% coverage,
zero lint findings, zero Bandit findings, zero open CodeQL alerts, average
cyclomatic complexity A, CI green on Python 3.10 through 3.13 — all gated, so
none of it can quietly regress.

## What we learned

**Don't invent a threshold when the detector publishes calibrated ones.** We
started with hand-picked score bands — clean under 25, suspect over 65. The
API publishes three operating points with their false-positive rates attached.
Using theirs isn't just more accurate, it's more defensible: where they
disagree, the track is borderline *by the detector's own account*, so the
contested band became something derived rather than asserted.

**A refusal is not an error.** The API declines to attribute a generator on
most tracks, answering `origin: "uncertain"`. Our first scorer counted that as
a misattribution — punishing the detector for exactly the behaviour the rest
of the tool exists to reward. Declining is now its own reported outcome.

**Build the guardrail so you can watch it work.** The mock detector spends the
call budget too. A guard that only runs when real money is on the line is a
guard nobody has ever seen work.

**A clean security scan means less than it looks.** Bandit passed while the
memo was still rendering `origin_map_evidence` straight into an `href` —
`html.escape` stops an attacker closing the attribute and does nothing about
the scheme, so a `javascript:` URL would have become a live link in a document
a buyer opens. Finding it needed someone to write the attack, not run the
tool.

## What's next for Catalog Risk Auditor

- **Scan at catalog scale.** 130 tracks are assembled; 16 were scanned. The
  `/bulk/jobs` endpoint is the right path for thousands, and the revenue join
  gets more interesting the longer the tail.
- **Hybrid analysis for the contested band.** `/api/analyze/hybrid` judges
  vocal and instrumental separately at 3× credits — exactly the right spend on
  the handful of assets a human would otherwise have to listen to, and it
  maps onto the IFPI/RIAA *AI-Generated vs AI-Assisted* distinction.
- **Let the analyst resolve the queue in place** and re-price live, so the
  escrow figure moves as decisions are made.
- **Price the platform-policy risk separately** from the copyright risk. They
  are different exposures on different timelines and currently share one
  number.
- **Take it to someone who buys catalogs for a living** and find out which
  parts of the memo they'd actually put in front of a counterparty.

## Honest limits

- **The demo catalog is 16 tracks** — 10 human, 6 AI — not the 130 assembled
  in the repo. Scanning costs one credit per track; we spent 22 in total.
- **Six planted tracks is a small sample.** Precision and recall are both
  1.00, and the memo says so itself rather than presenting them as measured
  rates. That caveat is generated automatically whenever fewer than ten tracks
  are planted, because a rate over a handful of tracks is a count wearing a
  decimal point.
- **The royalty figures are generated**, using a Pareto distribution with AI
  tracks earning less per track. The assumption is stated in the code and can
  be switched off with a flag.
- **The acquisition is hypothetical.** There is no Meridian Sound Library.
- **The detection results are real.** Every verdict came from a live API call.
  `out/api_evidence.json` is a complete, unedited response.

This tool produces an audio-authenticity assessment, not a legal opinion or a
valuation. Whether AI-generated works are enforceable is a question for
counsel.

## Built with

Python 3.10+ · HumanStandard detection API · zero runtime dependencies ·
`unittest` (275 tests, 90% coverage) · GitHub Actions and CodeQL (Python
3.10–3.13) · hand-written HTML, CSS and inline SVG, no template engine ·
`http.server` and server-sent events for the live UI · Internet Archive
netlabel collections · the SONICS dataset
