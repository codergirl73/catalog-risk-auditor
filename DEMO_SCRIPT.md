# Three-minute demo — two speakers

**A** frames the problem and reads the findings. **B** drives the screen.
Swap if the other split suits your voices better; the only thing that matters
is that one person is talking while the other is clicking.

Total spoken words: ~430. That is three minutes at an unhurried pace. Do not
speed up to fit more in — the pauses are what make it land.

---

## Before you hit record

| | |
|---|---|
| Tab 1 | `http://127.0.0.1:8766/` — the live API call (one unscored track) |
| Tab 2 | `http://127.0.0.1:8765/` — the replay (16 tracks, instant) |
| Tab 3 | `http://127.0.0.1:8765/memo.html` — the memo |
| Terminal | Optional, for the "zero dependencies" beat |

Check both tabs load **before** recording. Tab 1 spends one real credit and
takes about 48 seconds — that wait is deliberate and you talk over it.

---

## 0:00–0:20 · The gap — **A**

> Music catalogs sell for a multiple of annual royalties. A buyer checks who
> owns the songs and what they earn.
>
> Nobody checks whether a human made them.
>
> Work without human authorship may not be copyrightable. Platforms are
> demonetising synthetic tracks. The buyer finds out after the money is
> wired.

*Screen: Tab 1, idle.*

---

## 0:20–0:35 · Start the real call — **B**

> So we built the check. This track has never been scored. I'm clicking Run
> now — a live call to HumanStandard, about forty-five seconds. Let me tell
> you what it's doing while we wait.

*Click **Run audit** on Tab 1. Let the event log scroll.*

---

## 0:35–1:10 · What it does with the answer — **B**, then **A**

**B:**
> It uploads, gets a job ID, and polls until the verdict lands. But the
> verdict isn't the output. HumanStandard publishes three calibrated
> operating points, each with its false-positive rate attached — roughly
> zero, one to two percent, and five to ten.

**A:**
> We tier on those, not on numbers we invented. Suspect when the middle point
> calls it AI. Clean when even the widest net calls it human. And when they
> disagree, we don't guess — that's the contested band, and it goes to a
> person.

*The verdict lands. Read out the tier it returned.*

---

## 1:10–1:40 · The catalog — **B**

> That's one track. Here's a sixteen-track catalog already scored, so it
> replays instantly.

*Switch to Tab 2. Click **Start**, then step through with **Next** to the
evidence slide.*

> Every row here is a real API response. Six suspect, two contested, eight
> clean.

---

## 1:40–2:05 · The moment — **B**

*Stay on the evidence slide. Point at row six, then let the JSON show.*

> Look at this one. A real Udio generation. The headline verdict says
> **human**, at twenty-nine percent confidence — and its similarity map says
> twenty-four of its twenty-five nearest neighbours are verified human
> recordings.
>
> But the operating points say AI. We tier on those, so we caught it.
>
> A tool reading the obvious field would have passed a synthetic track
> straight into the clean base.

---

## 2:05–2:35 · The number — **A**

*B clicks **Next** to the summary slide.*

> Now the part that changes the price. Suspect assets are **37.5% of this
> catalog by count — but 17.4% of its revenue.**
>
> Synthetic uploads pile up faster than they earn. Count tracks and you
> overstate the damage by more than double. Only one of those numbers belongs
> in a negotiation.
>
> Against a seven-hundred-and-twenty-thousand-dollar asking price: a
> recommended escrow of **one hundred seventy-one thousand, seven hundred
> and ten dollars.**

---

## 2:35–2:50 · What it got right, and what it refused — **A**

> We planted six AI tracks. It caught all six, missed none, wrongly flagged
> zero humans. Six is a small sample — and the memo says so itself rather
> than quoting precision as if it were measured.
>
> And two it wouldn't call, carrying six thousand dollars between them.
> They're in the review queue with the reason and the evidence.

---

## 2:50–3:00 · Close — **B**

> Everyone else's detector outputs a score. This outputs a dollar figure, a
> review queue, and its own error rate.
>
> Zero dependencies, two hundred seventy-one tests, and every verdict you
> just saw came from a real API call.

---

## Between takes — get a fresh live call

The response is cached by file hash, so the same track will not make a second
live call. Stage another and just click Run again; the server picks it up
without restarting:

```bash
python3 scripts/stage_live_demo.py --ai      # expect a suspect verdict
python3 scripts/stage_live_demo.py --human   # expect clean
```

There are 6 AI and 102 human tracks left unscored, so takes are not the
constraint. An AI track gives the better live moment, but say which kind you
staged when you narrate it.

---

## If the live call is slow

If Tab 1 hasn't returned by 1:10, don't wait on it. Say:

> That's still running — it's real, so it takes as long as it takes. Here's
> one already scored.

...and move to Tab 2. Come back to Tab 1 at the end if it has landed. Never
let dead air run more than about five seconds.

---

## Don't say

- **"It detects AI music."** It doesn't — HumanStandard does. We price what
  HumanStandard found. That distinction is the whole project.
- **"100% accurate."** Six planted tracks. Say "caught all six of six" and
  let the number speak.
- **"Real catalog."** The audio is real and the verdicts are real; the
  catalog and its royalties are constructed, and the memo says so.
- Anything about the 130-track catalog as if it were all scanned. Sixteen
  were. Seventeen credits total.

---

## The numbers, for reference

| | |
|---|---|
| Tracks audited | 16 — 10 human, 6 AI |
| Clean / contested / suspect | 8 / 2 / 6 |
| Suspect by count | 37.5% |
| Suspect by revenue | 17.4% |
| Reported annual revenue | $48,000 |
| Asking price at 15× | $720,000 |
| **Recommended escrow** | **$171,710** |
| Planted AI caught | 6 of 6, 0 missed |
| Human tracks wrongly flagged | 0 |
| Contested, sent to review | 2 — $3,551 and $2,614 |
| Generator named correctly | 1 of 6; 5 declined to attribute |
| Credits spent | 17 of 200 |
| Live call time | ~48s per track |
