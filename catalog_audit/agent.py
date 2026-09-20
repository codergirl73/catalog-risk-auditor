"""The audit agent.

It states a plan, works through it, reacts to what comes back, escalates what
it cannot settle, and refuses to fold uncertain assets into a confident
headline. Events are yielded as it goes so a terminal or a UI can show the
work rather than a spinner.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import config, evaluation, tiering, valuation
from .detector import get_detector, sha256_file
from .models import Asset, AuditResult, Budget, Event, Tier

PLAN = [
    "Inventory the catalog folder and estimate the call budget",
    "Score every asset through HumanStandard, using cache where possible",
    "Sort assets into clean, contested and suspect against stated thresholds",
    "Escalate contested assets to a human review queue with reasons",
    "Join detection results to the seller's reported revenue",
    "Compute exposure and a recommended escrow",
    "Score the audit against ground truth, if labels were supplied",
]


def _ev(type_, title="", detail="", **data) -> Event:
    return Event(type=type_, title=title, detail=detail, data=data)


class AuditAgent:
    def __init__(self, force_mock: bool = False, budget_limit: int = None) -> None:
        limit = config.HS_CREDIT_BUDGET if budget_limit is None else budget_limit
        self.budget = Budget(limit=limit)
        self.detector = get_detector(force_mock=force_mock, budget=self.budget)
        self.result: AuditResult = None

    def run(self, catalog_dir, royalties_csv=None, truth_csv=None,
            multiple=None, asking_price=None, catalog_name=None):
        """Yield Events as the audit proceeds."""
        catalog_dir = Path(catalog_dir)
        result = AuditResult(
            catalog_name=catalog_name or catalog_dir.name,
            catalog_dir=str(catalog_dir),
            provider=self.detector.name,
            mock_mode=self.detector.is_mock,
            budget=self.budget,
        )
        self.result = result

        yield _ev("plan", "Audit plan", "%d steps" % len(PLAN), steps=PLAN)

        if self.detector.is_mock:
            yield _ev(
                "warn", "Mock detector active",
                "No HS_API_KEY is set. Scores below are derived from file "
                "hashes and mean nothing. Do not record a demo in this mode.",
            )

        # 1. inventory --------------------------------------------------
        yield _ev("step", PLAN[0])
        files = self._inventory(catalog_dir)
        if not files:
            yield _ev("error", "Empty catalog",
                      "No audio files found under %s" % catalog_dir)
            return

        yield _ev(
            "tool_result", "inventory",
            "%d audio files, %.1f MB total"
            % (len(files), sum(f.stat().st_size for f in files) / 1e6),
            count=len(files),
        )

        # Hash every file once. The digest is both the cache key and the
        # evidence trail, so doing it up front means the budget estimate below
        # is a fact about this catalog rather than an assumption.
        digests = {f: sha256_file(f) for f in files}
        already = sum(1 for f in files if self.detector.is_cached(digests[f]))
        needed = len(files) - already

        yield _ev(
            "tool_result", "budget check",
            "%d of %d already cached. %s"
            % (already, len(files), config.budget_summary(needed)),
            needed=needed, cached=already, limit=self.budget.limit,
        )
        yield _ev(
            "tool_result", "runtime estimate",
            config.runtime_estimate(min(needed, self.budget.limit)),
        )

        if needed > self.budget.limit:
            yield _ev(
                "warn", "Catalog exceeds the call budget",
                "%d assets need a live call and the budget is %d. %d will be "
                "scored; the remaining %d are reported unscored rather than "
                "assumed clean. Raise HS_CREDIT_BUDGET or narrow the catalog."
                % (needed, self.budget.limit, self.budget.limit,
                   needed - self.budget.limit),
            )

        # 2. score ------------------------------------------------------
        yield _ev("step", PLAN[1],
                  "detector: %s | budget %d" % (self.detector.name,
                                                self.budget.limit))
        assets = []
        cached_n = 0
        failed_n = 0
        for i, path in enumerate(files, 1):
            score = self.detector.detect(path, digest=digests[path])
            if score.cached:
                cached_n += 1
            if not score.ok:
                failed_n += 1
            assets.append(Asset(filename=path.name, score=score))
            if i % 10 == 0 or i == len(files):
                yield _ev(
                    "tool_result", "humanstandard.detect",
                    "%d/%d scored (%d from cache, %d failed)"
                    % (i, len(files), cached_n, failed_n),
                    done=i, total=len(files),
                )
        result.assets = assets

        # HumanStandard's ?mock= fixtures come back over the real API, from a
        # real key, with real field shapes -- and carry "mock": true. A run
        # built on them is a mock run no matter which detector produced it, so
        # the flag is promoted onto the result and the memo refuses it.
        mocked = [a for a in assets if a.score and a.score.mock]
        if mocked:
            result.mock_mode = True
            scenarios = sorted({a.score.mock_scenario for a in mocked
                                if a.score.mock_scenario})
            yield _ev(
                "warn", "API mock responses detected",
                "%d of %d responses were HumanStandard fixtures%s, not real "
                "detections. The run is marked mock and the memo will refuse "
                "to render. Unset HS_MOCK_SCENARIO for a real audit."
                % (len(mocked), len(assets),
                   " (%s)" % ", ".join(scenarios) if scenarios else ""),
            )

        yield _ev(
            "tool_result", "budget spent",
            "%d live calls spent of %d budgeted, %d served from cache, "
            "%d skipped for lack of budget"
            % (self.budget.spent, self.budget.limit,
               self.budget.served_from_cache, self.budget.skipped),
            spent=self.budget.spent, remaining=self.budget.remaining,
        )

        if self.budget.skipped:
            yield _ev(
                "warn", "Budget exhausted mid-run",
                "%d assets were never scored. They are counted as detection "
                "failures and excluded from the clean base."
                % self.budget.skipped,
            )

        if failed_n:
            yield _ev(
                "warn", "Detection failures",
                "%d of %d assets could not be scored and are excluded from the "
                "clean base rather than assumed safe." % (failed_n, len(files)),
            )

        # 3. tier -------------------------------------------------------
        yield _ev("step", PLAN[2], config.thresholds_summary())
        review = tiering.apply(assets)
        counts = tiering.counts(assets)
        yield _ev(
            "finding", "Tier breakdown",
            "clean %d | contested %d | suspect %d | error %d"
            % (counts.get(Tier.CLEAN, 0), counts.get(Tier.CONTESTED, 0),
               counts.get(Tier.SUSPECT, 0), counts.get(Tier.ERROR, 0)),
            counts={k.value: v for k, v in counts.items()},
        )

        # 4. escalate ---------------------------------------------------
        yield _ev("step", PLAN[3])
        result.review_queue = [
            {"filename": a.filename,
             "ai_score": a.ai_score,
             "annual_usd": a.annual_usd,
             "reason": a.notes[-1] if a.notes else ""}
            for a in review
        ]
        yield _ev(
            "tool_result", "review_queue",
            "%d assets routed to human review" % len(review),
            count=len(review),
        )

        # 5. revenue ----------------------------------------------------
        yield _ev("step", PLAN[4])
        if royalties_csv:
            royalties = valuation.load_royalties(royalties_csv)
            matched = valuation.attach_royalties(assets, royalties)
            yield _ev(
                "tool_result", "load_royalties",
                "%d of %d assets matched to a revenue row" % (matched, len(assets)),
            )
            if matched < len(assets):
                yield _ev(
                    "warn", "Unmatched assets",
                    "%d assets carry no reported revenue and contribute nothing "
                    "to the exposure figure." % (len(assets) - matched),
                )
        else:
            yield _ev("tool_result", "load_royalties",
                      "No revenue sheet supplied; exposure will be by count only.")

        # 6. value ------------------------------------------------------
        yield _ev("step", PLAN[5])
        val = valuation.value(assets, multiple=multiple,
                              asking_price_usd=asking_price)
        result.valuation = val
        yield _ev("verdict", "Exposure", valuation.headline(val),
                  valuation=val.__dict__)

        # 7. evaluate ---------------------------------------------------
        yield _ev("step", PLAN[6])
        if truth_csv:
            truth = evaluation.load_ground_truth(truth_csv)
            matched = evaluation.attach(assets, truth)
            ev = evaluation.evaluate(assets)
            result.evaluation = ev
            yield _ev("finding", "Accuracy against ground truth",
                      evaluation.summary(ev), labelled=matched)
        else:
            yield _ev("tool_result", "evaluate",
                      "No ground-truth file supplied; no accuracy reported.")

        result.manifest_sha256 = self._manifest(assets)
        yield _ev("done", "Audit complete",
                  "manifest %s" % result.manifest_sha256[:16])

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _inventory(catalog_dir: Path) -> list:
        out = [
            p for p in sorted(catalog_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() in config.AUDIO_EXTENSIONS
        ]
        return out

    @staticmethod
    def _manifest(assets: list) -> str:
        """One hash over the whole audited set, for the audit trail."""
        h = hashlib.sha256()
        for a in sorted(assets, key=lambda x: x.filename):
            h.update(a.filename.encode())
            if a.score:
                h.update(a.score.sha256.encode())
        return h.hexdigest()
