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

# How often the scoring loop reports progress.
PROGRESS_EVERY = 10

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
    """Runs one catalog audit, emitting Events as it goes.

    Holds the call budget and the detector, and carries the products of
    each step between them. Stateful by design: the steps are a pipeline
    and each one needs what the last produced.
    """

    def __init__(self, force_mock: bool = False,
                 budget_limit: int | None = None) -> None:
        limit = config.HS_CREDIT_BUDGET if budget_limit is None else budget_limit
        self.budget = Budget(limit=limit)
        self.detector = get_detector(force_mock=force_mock, budget=self.budget)
        self.result: AuditResult | None = None
        # Products of the steps, shared between them in PLAN order.
        self._digests: dict = {}
        self._assets: list = []
        self._review: list = []

    def run(self, catalog_dir, royalties_csv=None, truth_csv=None,
            multiple=None, asking_price=None, catalog_name=None):
        """Yield Events as the audit proceeds.

        Each numbered step is its own generator, in the order PLAN declares
        them. A test asserts the steps executed equal the steps announced, so
        the plan cannot drift away from the work.
        """
        catalog_dir = Path(catalog_dir)
        self.result = AuditResult(
            catalog_name=catalog_name or catalog_dir.name,
            catalog_dir=str(catalog_dir),
            provider=self.detector.name,
            mock_mode=self.detector.is_mock,
            budget=self.budget,
        )

        yield _ev("plan", "Audit plan", "%d steps" % len(PLAN), steps=PLAN)

        if self.detector.is_mock:
            yield _ev(
                "warn", "Mock detector active",
                "No HS_API_KEY is set. Scores below are derived from file "
                "hashes and mean nothing. Do not record a demo in this mode.",
            )

        files, escaping = self._partition(
            self._find_audio(catalog_dir), catalog_dir)
        yield from self._step_inventory(catalog_dir, files, escaping)
        if not files:
            return

        yield from self._step_score(files)
        yield from self._step_tier()
        yield from self._step_escalate()
        yield from self._step_revenue(royalties_csv)
        yield from self._step_value(multiple, asking_price)
        yield from self._step_evaluate(truth_csv)

        self.result.manifest_sha256 = self._manifest(self._assets)
        yield _ev("done", "Audit complete",
                  "manifest %s" % self.result.manifest_sha256[:16])

    # -- steps -----------------------------------------------------------

    def _step_inventory(self, catalog_dir: Path, files: list,
                        escaping: list = ()):
        """Count the catalog, then price the run before committing to it."""
        yield _ev("step", PLAN[0])

        if escaping:
            yield _ev(
                "warn", "Links pointing outside the catalog",
                "%d file%s in this catalog resolve outside it and were not "
                "uploaded: %s. A catalog is assembled by the seller, so a "
                "link out of it would send a file of their choosing to the "
                "detection API. Set FOLLOW_EXTERNAL_SYMLINKS=1 if you "
                "assembled this catalog yourself."
                % (len(escaping), "" if len(escaping) == 1 else "s",
                   ", ".join(p.name for p in escaping[:5])),
                count=len(escaping),
            )

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
        # evidence trail, so doing it up front makes the estimate below a fact
        # about this catalog rather than an assumption, and the work is reused
        # by the detection call instead of repeated.
        self._digests = {f: sha256_file(f) for f in files}
        already = sum(1 for f in files
                      if self.detector.is_cached(self._digests[f]))
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

    def _step_score(self, files: list):
        """Score every asset, reporting progress and what it cost."""
        yield _ev("step", PLAN[1],
                  "detector: %s | budget %d" % (self.detector.name,
                                                self.budget.limit))
        assets, cached_n, failed_n = [], 0, 0
        for i, path in enumerate(files, 1):
            score = self.detector.detect(path, digest=self._digests[path])
            cached_n += 1 if score.cached else 0
            failed_n += 0 if score.ok else 1
            assets.append(Asset(filename=path.name, score=score))
            if i % PROGRESS_EVERY == 0 or i == len(files):
                yield _ev(
                    "tool_result", "humanstandard.detect",
                    "%d/%d scored (%d from cache, %d failed)"
                    % (i, len(files), cached_n, failed_n),
                    done=i, total=len(files),
                )

        self._assets = assets
        self.result.assets = assets

        yield from self._warn_mock_responses(assets)
        yield from self._report_budget(assets, failed_n)

    def _warn_mock_responses(self, assets: list):
        """A fixture from the real API is still a fixture.

        HumanStandard's ?mock= responses arrive over the real endpoint, from a
        real key, with real field shapes -- and carry "mock": true. Promoting
        that onto the result is what stops one reaching a memo.
        """
        mocked = [a for a in assets if a.score and a.score.mock]
        if not mocked:
            return
        self.result.mock_mode = True
        scenarios = sorted({a.score.mock_scenario for a in mocked
                            if a.score.mock_scenario})
        yield _ev(
            "warn", "API mock responses detected",
            "%d of %d responses were HumanStandard fixtures%s, not real "
            "detections. The run is marked mock and the memo will refuse to "
            "render. Unset HS_MOCK_SCENARIO for a real audit."
            % (len(mocked), len(assets),
               " (%s)" % ", ".join(scenarios) if scenarios else ""),
        )

    def _report_budget(self, assets: list, failed_n: int):
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
                "clean base rather than assumed safe."
                % (failed_n, len(assets)),
            )

    def _step_tier(self):
        yield _ev("step", PLAN[2], config.thresholds_summary())
        self._review = tiering.apply(self._assets)
        counts = tiering.counts(self._assets)
        yield _ev(
            "finding", "Tier breakdown",
            "clean %d | contested %d | suspect %d | error %d"
            % (counts.get(Tier.CLEAN, 0), counts.get(Tier.CONTESTED, 0),
               counts.get(Tier.SUSPECT, 0), counts.get(Tier.ERROR, 0)),
            counts={k.value: v for k, v in counts.items()},
        )

    def _step_escalate(self):
        """Announce the queue. It is assembled after the revenue join below,
        because an entry is only actionable once it carries what the asset
        earns -- a reviewer with an hour should spend it on the assets that
        matter, not on the top of the alphabet."""
        yield _ev("step", PLAN[3])
        yield _ev(
            "tool_result", "review_queue",
            "%d assets routed to human review" % len(self._review),
            count=len(self._review),
        )

    def _step_revenue(self, royalties_csv):
        yield _ev("step", PLAN[4])
        if royalties_csv:
            royalties = valuation.load_royalties(royalties_csv)
            matched = valuation.attach_royalties(self._assets, royalties)
            yield _ev(
                "tool_result", "load_royalties",
                "%d of %d assets matched to a revenue row"
                % (matched, len(self._assets)),
            )
            if matched < len(self._assets):
                yield _ev(
                    "warn", "Unmatched assets",
                    "%d assets carry no reported revenue and contribute "
                    "nothing to the exposure figure."
                    % (len(self._assets) - matched),
                )
        else:
            yield _ev("tool_result", "load_royalties",
                      "No revenue sheet supplied; exposure will be by count "
                      "only.")

        self.result.review_queue = [
            {"filename": a.filename,
             "ai_score": a.ai_score,
             "annual_usd": a.annual_usd,
             "reason": a.notes[-1] if a.notes else ""}
            for a in sorted(self._review, key=lambda a: -a.annual_usd)
        ]

    def _step_value(self, multiple, asking_price):
        yield _ev("step", PLAN[5])
        val = valuation.value(self._assets, multiple=multiple,
                              asking_price_usd=asking_price)
        self.result.valuation = val
        yield _ev("verdict", "Exposure", valuation.headline(val),
                  valuation=val.__dict__)

    def _step_evaluate(self, truth_csv):
        yield _ev("step", PLAN[6])
        if not truth_csv:
            yield _ev("tool_result", "evaluate",
                      "No ground-truth file supplied; no accuracy reported.")
            return
        truth = evaluation.load_ground_truth(truth_csv)
        origins = evaluation.load_origins(truth_csv)
        matched = evaluation.attach(self._assets, truth, origins)
        self.result.evaluation = evaluation.evaluate(self._assets)
        yield _ev("finding", "Accuracy against ground truth",
                  evaluation.summary(self.result.evaluation), labelled=matched)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _find_audio(catalog_dir: Path) -> list:
        """Audio files inside the catalog, in a stable order."""
        return [
            p for p in sorted(catalog_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() in config.AUDIO_EXTENSIONS
        ]

    @staticmethod
    def _escapes_catalog(path: Path, catalog_dir: Path) -> bool:
        """Whether `path` resolves outside the catalog it was found in.

        A symlink is the one way a file can appear to be in the catalog while
        actually being somewhere else on the machine. Since the catalog is
        assembled by the counterparty, following one would upload a file of
        their choosing to a third party.
        """
        try:
            root = catalog_dir.resolve(strict=False)
            return not path.resolve(strict=False).is_relative_to(root)
        except (OSError, ValueError):
            return True

    @classmethod
    def _partition(cls, files: list, catalog_dir: Path) -> tuple:
        """Split the inventory into (inside the catalog, escaping it)."""
        if config.FOLLOW_EXTERNAL_SYMLINKS:
            return files, []
        inside, escaping = [], []
        for path in files:
            (escaping if cls._escapes_catalog(path, catalog_dir)
             else inside).append(path)
        return inside, escaping

    @staticmethod
    def _manifest(assets: list) -> str:
        """One hash over the whole audited set, for the audit trail."""
        digest = hashlib.sha256()
        for asset in sorted(assets, key=lambda a: a.filename):
            digest.update(asset.filename.encode())
            if asset.score:
                digest.update(asset.score.sha256.encode())
        return digest.hexdigest()
