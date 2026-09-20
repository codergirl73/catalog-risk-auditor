"""Tiering rules, including the low-confidence override."""

import unittest

from catalog_audit import config
from catalog_audit.models import Asset, Tier, TrackScore
from catalog_audit.tiering import apply, classify, counts


def score(ai, conf=0.9, error=""):
    return TrackScore(filename="t.mp3", path="t.mp3", ai_score=ai,
                      confidence=conf, provider="test", error=error)


class TestClassify(unittest.TestCase):
    def test_clean_below_ceiling(self):
        tier, reason = classify(score(config.CLEAN_CEILING - 1))
        self.assertEqual(tier, Tier.CLEAN)
        self.assertIn("clean ceiling", reason)

    def test_suspect_above_floor(self):
        tier, _ = classify(score(config.SUSPECT_FLOOR + 1))
        self.assertEqual(tier, Tier.SUSPECT)

    def test_band_is_contested(self):
        midpoint = (config.CLEAN_CEILING + config.SUSPECT_FLOOR) / 2
        tier, _ = classify(score(midpoint))
        self.assertEqual(tier, Tier.CONTESTED)

    def test_boundaries_are_not_suspect(self):
        # Exactly on a threshold falls inside the contested band, never outside it.
        self.assertEqual(classify(score(config.CLEAN_CEILING))[0], Tier.CONTESTED)
        self.assertEqual(classify(score(config.SUSPECT_FLOOR))[0], Tier.CONTESTED)

    def test_low_confidence_overrides_a_confident_looking_score(self):
        tier, reason = classify(score(95.0, conf=config.MIN_CONFIDENCE - 0.01))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("confidence", reason)

    def test_error_scores_are_not_treated_as_clean(self):
        tier, _ = classify(score(-1.0, error="timeout"))
        self.assertEqual(tier, Tier.ERROR)


class TestApply(unittest.TestCase):
    def test_review_queue_holds_contested_and_errors(self):
        assets = [
            Asset(filename="a", score=score(5)),
            Asset(filename="b", score=score(45)),
            Asset(filename="c", score=score(90)),
            Asset(filename="d", score=score(-1, error="boom")),
        ]
        review = apply(assets)
        self.assertEqual({a.filename for a in review}, {"b", "d"})

        c = counts(assets)
        self.assertEqual(c[Tier.CLEAN], 1)
        self.assertEqual(c[Tier.SUSPECT], 1)
        self.assertEqual(c[Tier.CONTESTED], 1)
        self.assertEqual(c[Tier.ERROR], 1)

    def test_missing_score_is_an_error_not_a_pass(self):
        assets = [Asset(filename="x", score=None)]
        apply(assets)
        self.assertEqual(assets[0].tier, Tier.ERROR)


if __name__ == "__main__":
    unittest.main()


def tiered(press, human_safe, recall, **kw):
    """A score carrying HumanStandard's three calibrated operating points."""
    base = dict(filename="t.mp3", path="t.mp3", ai_score=50.0,
                confidence=0.9, provider="humanstandard",
                tier_verdicts={"press_safe": press, "human_safe": human_safe,
                               "recall": recall})
    base.update(kw)
    return TrackScore(**base)


class TestCalibratedTiers(unittest.TestCase):
    """Tiering on HumanStandard's own operating points, not invented numbers."""

    def test_ai_at_all_tiers_is_suspect(self):
        tier, why = classify(tiered("ai", "ai", "ai"))
        self.assertEqual(tier, Tier.SUSPECT)
        self.assertIn("human-safe", why)

    def test_human_at_all_tiers_is_clean(self):
        tier, why = classify(tiered("human", "human", "human"))
        self.assertEqual(tier, Tier.CLEAN)
        self.assertIn("every operating point", why)

    def test_split_verdict_is_contested(self):
        # Their docs: ai at recall but human at human-safe means borderline.
        tier, why = classify(tiered("uncertain", "human", "ai"))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("disagree", why)

    def test_uncertain_everywhere_is_contested_not_clean(self):
        tier, _ = classify(tiered("uncertain", "uncertain", "uncertain"))
        self.assertEqual(tier, Tier.CONTESTED)

    def test_ai_at_human_safe_but_not_press_safe_is_still_suspect(self):
        # Good enough to auto-reject at distribution is good enough to escrow.
        tier, _ = classify(tiered("uncertain", "ai", "ai"))
        self.assertEqual(tier, Tier.SUSPECT)

    def test_suspected_industry_label_never_reads_clean(self):
        tier, why = classify(tiered("uncertain", "uncertain", "ai",
                                    headline_verdict="ai_generated_suspected"))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("stem-level", why)

    def test_low_confidence_still_forces_review(self):
        tier, why = classify(tiered("uncertain", "uncertain", "uncertain",
                                    confidence=0.2))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("confidence", why)

    def test_calibrated_path_beats_the_score_fallback(self):
        # A score that would read clean, but tiers that say AI.
        tier, _ = classify(tiered("ai", "ai", "ai", ai_score=1.0))
        self.assertEqual(tier, Tier.SUSPECT)

    def test_score_fallback_applies_without_tier_verdicts(self):
        s = TrackScore(filename="t.mp3", path="t.mp3", ai_score=95.0,
                       confidence=0.9, provider="other")
        tier, why = classify(s)
        self.assertEqual(tier, Tier.SUSPECT)
        self.assertIn("suspect floor", why)


class TestReviewerEvidence(unittest.TestCase):
    """An escalation without evidence is a shrug. Reasons carry the detail."""

    def test_industry_label_is_named_in_the_reason(self):
        _, why = classify(tiered("ai", "ai", "ai",
                                 industry_label="AI-Generated",
                                 industry_label_status="meets_definition"))
        self.assertIn("IFPI/RIAA", why)

    def test_origin_attribution_reaches_the_reason(self):
        _, why = classify(tiered(
            "ai", "ai", "ai", origin="suno",
            origin_summary="24 of its 25 nearest reference recordings are "
                           "Suno generations."))
        self.assertIn("Suno", why)

    def test_peak_risk_is_timestamped_for_the_reviewer(self):
        _, why = classify(tiered("uncertain", "human", "ai",
                                 risk_timeline=[0.1, 0.1, 0.1, 0.1, 0.95]))
        self.assertIn("0:10", why)

    def test_a_quiet_timeline_is_not_reported_as_a_peak(self):
        _, why = classify(tiered("uncertain", "human", "ai",
                                 risk_timeline=[0.1, 0.2, 0.15]))
        self.assertNotIn("Risk peaks", why)
