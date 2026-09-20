**[▶ Watch a real audit run](https://codergirl73.github.io/catalog-risk-auditor/)** · **[read the memo it produced](https://codergirl73.github.io/catalog-risk-auditor/memo.html)**

## Inspiration

Music catalogs are bought and sold as income streams. A buyer pays a multiple
of annual royalties — commonly 10–20× — and diligence checks the things that
have always mattered: who owns the songs, whether there are disputes,
whether samples were cleared, whether the revenue is durable.

Nobody checks whether a person made the recordings.

That gap stopped being theoretical some time ago. Platforms now report
AI-generated material arriving at a scale where any recently assembled
catalog is statistically likely to contain some, whether or not the seller
knows. And it matters three ways that compound:

1. **Ownership.** Work produced without meaningful human authorship isn't
   protectable under US copyright. Part of the catalog may not be ownable.
2. **Monetisation.** Platforms are demonetising synthetic content. Income a
   seller reports as recurring can be switched off by a policy change.
3. **Revenue quality.** Where synthetic uploads are paired with stream fraud,
   the reported income was never real.

The buyer finds out after closing, when the money is already wired.

I wanted to build the thing a diligence analyst would actually open — not a
classifier demo. The difference is that a classifier outputs a score, and an
analyst needs a number they can take into a negotiation.

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
acquisition multiple $m$, because the buyer is paying a multiple, not a year.

## The finding worth leading with

On the demonstration catalog, suspect assets are **37.5% of the catalog by
count but 17.4% of its revenue.**

That gap is the whole argument. Synthetic uploads accumulate far faster than
they earn, so a count-based read of a catalog overstates the damage by more
than a factor of two. A seller saying "only a few of my tracks are affected"
and a buyer saying "a third of this catalog is synthetic" can both be telling
the truth about different numbers. Only one of those numbers changes the
price, and it's the one nobody was computing.

## The moment that justified the design

One planted track did more to validate the approach than any amount of
testing.

`sonics__fake_00524_udio_1.mp3` is a real Udio generation. HumanStandard's
top-level verdict on it is **`"human"`**, at 29% confidence. Its similarity
map reports that *24 of its 25 nearest reference recordings are verified
human recordings.* Read the obvious field and you wave it into the clean base.

But the API also returns three **calibrated operating points**, each published
with its false-positive rate:

| Operating point | FPR | Verdict on this track |
|---|---|---|
| `press_safe` | ~0% | human |
| `human_safe` | ~1–2% | **ai** |
| `recall` | ~5–10% | **ai** |

This tool tiers on those, not on the headline verdict. So the track is caught
and escrowed — and the memo states the disagreement on the asset's own row,
rather than quietly preferring one number over the other.

A buyer being told an asset is suspect deserves to know the top-line verdict
said otherwise, and why the calibrated number is the one to act on.

## How I built it

**Python 3.10+, standard library only. Zero dependencies.** No
`requirements.txt`, no lockfile, no virtualenv — `urllib.request` for HTTP,
`csv` for data, `hashlib` for the audit trail, `dataclasses` throughout. CI
asserts the dependency tree is still empty on every push rather than leaving
it as a claim in a README. An empty dependency tree cannot carry a transitive
vulnerability.

The pipeline is nine small modules, each with one job: config, models,
detector, tiering, valuation, evaluation, memo, agent, CLI.

**The agent states a plan and then executes exactly that plan** — there's a
test asserting the executed steps equal the declared ones, so the plan can't
drift from the work. It estimates its spend before spending, scores, tiers,
escalates what it can't settle, joins to revenue, and finally grades itself.

**Building the ground truth honestly was the part I cared most about.** The
human half is 116 real CC-licensed recordings from Internet Archive netlabel
collections. The AI half is real Suno and Udio generations from the SONICS
dataset (Rahman et al., ICLR 2025, CC BY-NC 4.0).

I deliberately did *not* synthesise audio and label it "AI". It would have
been faster and it would have made precision and recall meaningless — and the
error rate is the one number I most wanted to be able to state.

Because SONICS encodes the generating model in each filename, the answer key
carries not just "is this AI" but "which model made it", so the audit grades
**attribution** as well as detection.

## Challenges

**The API is asynchronous, and I'd assumed it wasn't.** `POST /api/analyze`
returns a `job_id`; the verdict arrives by polling
`/api/jobs/{job_id}/status`. My first client expected the verdict in the POST
response and would have failed on every single call.

**Cloudflare, not authentication.** The first live request came back `403` with
`error code: 1010`. That reads exactly like a rejected API key, and I nearly
went looking for one. It's a Cloudflare block on `urllib`'s default
`Python-urllib/3.x` user agent. Setting a real User-Agent fixed it.
`api.hsverify.com` — the host I'd guessed — doesn't resolve at all.

**The live API differs from its own documentation**, in four ways I only found
by looking at real responses:

- `ai_probability` is present on every response and is a direct 0–1
  likelihood. It's a better score than reconstructing one from verdict plus
  confidence.
- `verdict` has a fourth value, `"suspicious"`, which the docs don't list. It
  means the screening threshold cleared and the certification threshold
  didn't — the contested band, stated in their vocabulary instead of mine.
- The timeline arrives as `risk_segments_full_mix` with real start/end
  seconds, not the documented flat `risk_timeline`.
- `industry_label` comes back `"ai_generated"`, and `industry_label_basis` —
  an array of plain-language reasons absent from the response table entirely
  — turned out to be the best short evidence line the API produces.

I found all of these using **HumanStandard's `?mock=` mode**, which returns
real-shaped fixtures and bills nothing. The full submit→poll→map→tier cycle
was verified for zero credits before I spent a single one.

**200 credits, one per track.** Detection credits are finite and a catalog is
arbitrarily large, so the agent is given an allowance rather than trusted to
stop. Budget is decremented *before* each request, because a request that
times out may still have been charged. Anything left unscored when the budget
runs out is reported as unscored — **never as clean**. An asset nobody paid to
check is not an asset anybody verified.

**Planting real AI tracks without downloading 3.8 GB.** The SONICS archive is
3.8 GB and I needed a dozen songs. HuggingFace serves byte ranges, and a ZIP
keeps its index at the end of the file, so I wrote a small seekable file-like
object over HTTP ranges and handed it to the standard library's `zipfile`
module, which read the central directory and inflated only the members I
asked for. About 30 MB crossed the wire. Still zero dependencies.

**A palette that failed the people it was for.** The risk scale was
green/amber/red. Run through a colour-vision validator it scores ΔE 0.5
between *contested* and *suspect* under deuteranopia — a red-green colourblind
reader cannot distinguish two of the three tiers, in a document whose only job
is ranking risk. It's now blue/amber/red, which separates under every CVD
type, and every segment carries a label so colour is never the sole signal.

## What I learned

**Don't invent a threshold when the detector publishes calibrated ones.** I
started with hand-picked score bands — clean under 25, suspect over 65. The
API publishes three operating points with their false-positive rates
attached. Using theirs isn't just more accurate, it's more defensible: where
the operating points disagree, the track is borderline *by the detector's own
account*, so the contested band became something derived rather than asserted.

**A refusal is not an error.** The API declines to attribute a generator on
most tracks, answering `origin: "uncertain"`. My first scorer counted that as
a misattribution — punishing the detector for exactly the behaviour the rest
of the tool exists to reward. Declining is now its own reported outcome.

**Build the guardrail so you can watch it work.** The mock detector spends the
call budget too. A guard that only runs when real money is on the line is a
guard nobody has ever seen work.

## Honest limits

- **The demo catalog is 16 tracks** — 10 human, 6 AI — not the 130 assembled
  in the repo. Scanning costs one credit per track and I spent 17 in total.
- **Six planted tracks is a small sample.** Precision and recall are both
  1.00, and the memo says in as many words that these are counts rather than
  measured rates. That caveat is generated automatically whenever fewer than
  ten tracks are planted, because a rate over a handful of tracks is a count
  wearing a decimal point.
- **The royalty figures are generated**, using a Pareto distribution with AI
  tracks earning less per track. The assumption is stated in the code and can
  be switched off with a flag.
- **The acquisition is hypothetical.** There is no Meridian Sound Library.
- **The detection results are real.** Every verdict came from a live API call.
  `out/api_evidence.json` is a raw response.

This tool produces an audio-authenticity assessment, not a legal opinion or a
valuation. Whether AI-generated works are enforceable is a question for
counsel.

## Built with

Python 3.10+ · HumanStandard detection API · zero runtime dependencies ·
`unittest` (143 tests) · GitHub Actions (Python 3.10–3.13) · hand-written
HTML and inline SVG, no template engine · Internet Archive netlabel
collections · the SONICS dataset
