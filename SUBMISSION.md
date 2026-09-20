# Submission notes

Fill the `[ ]` placeholders from a real run before submitting.

---

## Devpost requirements (HumanStandard track)

- [ ] Public code repository link
- [ ] Demo video, 3 minutes or less, end to end
- [ ] Written description: the problem and the intended industry user
- [ ] Explanation of how the project integrates the HumanStandard API and
      processes its results
- [ ] Evidence of at least one real API call (screenshot or log)
- [ ] Every team member listed

---

## Problem and intended user

**User:** a catalog acquisition analyst at a music rights fund, publisher or
label — the person who runs due diligence before the fund wires money.

**Problem:** catalogs are priced at a multiple of annual royalties. Diligence
verifies ownership chain, disputes, uncleared samples and revenue stability. It
does not verify that a human made the recordings. With AI uploads exceeding
half of daily new music on some platforms, a recently assembled catalog can
carry material that (a) may not be protectable under US copyright, (b) can be
demonetised by platform policy, and (c) may show streaming revenue that was
never real. The buyer finds out after closing.

---

## How the HumanStandard API is integrated

Every audio asset in the catalog is submitted to the HumanStandard detection
endpoint. Responses are mapped to a normalised score and the detector's own
confidence, then cached on disk keyed by SHA-256 of the file so a catalog is
scored once and analysed many times.

The API result is not the output. It is one input to a decision:

1. Score and confidence are read separately. A high score with low confidence
   is not treated as a finding.
2. Assets are tiered against stated, printable thresholds.
3. Anything in the contested band, or below the confidence floor, is escalated
   to a human review queue with the reason recorded.
4. Tier decisions are joined to the seller's revenue sheet, which is where the
   result becomes a price.
5. Where the catalog carries ground-truth labels, the agent's own accuracy is
   measured and reported alongside the finding.

---

## Results from the demo run

- Catalog size: `[ ]` tracks
- Reported annual revenue: `[ ]`
- Clean / contested / suspect: `[ ]` / `[ ]` / `[ ]`
- Suspect share by count: `[ ]`% — by revenue: `[ ]`%
- Recommended escrow: `[ ]`
- Precision / recall against planted labels: `[ ]` / `[ ]`
- Human tracks wrongly flagged: `[ ]`

> The line worth saying out loud: suspect tracks are `[ ]`% of the catalog by
> count but only `[ ]`% of revenue. Synthetic uploads accumulate far faster
> than they earn.

---

## Disclosure

The catalog and its royalty figures are constructed for demonstration. The
audio is real, CC-licensed, human-made music from Internet Archive netlabel
collections; the AI tracks were generated deliberately and labelled so that
accuracy could be measured; the HumanStandard API responses are real; the
acquisition scenario is hypothetical.

---

## The Code Registry track

Angle: the project is **zero-dependency**. Everything runs on the Python
standard library, so the dependency surface is empty and there is no supply
chain to audit. Add: MIT licence, no secrets in the repo, `.env` gitignored,
unit tests with no test framework required, docstrings throughout.

- [ ] Repository is public
- [ ] `.env` is not committed — check `git log -p | grep -i api_key`
- [ ] `python3 -m unittest discover -s tests` passes

---

## Open Track

Same project, framed on the agent loop rather than the music: a tool that
plans, calls an external API under a budget, escalates what it cannot resolve,
refuses to fold uncertainty into a confident number, and reports its own error
rate.

---

## Demo script, 3 minutes

| Time | Beat |
|---|---|
| 0:00–0:20 | The setup. "A fund is about to pay $[ ] for this catalog." Show the folder. The three numbers: 90,000 AI uploads a day, 85% of their streams fraudulent, zero tools to check. |
| 0:20–1:40 | The agent runs. Plan, then scoring, then tiering against visible thresholds, then escalation. |
| 1:40–2:20 | The memo. Lead with the escrow figure. Then the count-vs-revenue line. |
| 2:20–2:45 | Accuracy. "I planted [ ] AI tracks. It caught [ ], missed [ ], wrongly flagged [ ] humans — all of them lo-fi." |
| 2:45–3:00 | The close: "[ ] tracks I couldn't call from audio. I'm not guessing on a $[ ] decision — those go to a human, and here's the list." |

---

## If you fall behind, cut in this order

1. `--json` output and anything reading `audit.json`
2. Print styling on the memo
3. The review-queue table in the memo (keep the count)
4. Colour in the terminal output

**Never cut:** one real API call in evidence, the three tiers, the revenue
join, the human-review list.
