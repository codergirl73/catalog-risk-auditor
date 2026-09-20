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
        # the reason must name all three operating points so a reviewer can
        # see exactly where the detector split
        self.assertIn("press-safe uncertain", why)
        self.assertIn("human-safe human", why)
        self.assertIn("recall ai", why)

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
        self.assertIn("stem-level", why.lower())

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


def verdicted(verdict, confidence=0.95, **kw):
    """A score shaped like a real live response: verdict, no tier_verdicts."""
    base = dict(filename="t.mp3", path="t.mp3", ai_score=50.0,
                confidence=confidence, provider="humanstandard",
                verdict=verdict)
    base.update(kw)
    return TrackScore(**base)


class TestVerdictPath(unittest.TestCase):
    """The path that actually runs: live responses carry no tier_verdicts."""

    def test_ai_verdict_is_suspect(self):
        tier, why = classify(verdicted("ai"))
        self.assertEqual(tier, Tier.SUSPECT)
        self.assertIn("verdict of AI", why)

    def test_human_verdict_is_clean(self):
        tier, _ = classify(verdicted("human"))
        self.assertEqual(tier, Tier.CLEAN)

    def test_suspicious_is_contested(self):
        # "suspicious" is a real value the published docs do not list.
        tier, why = classify(verdicted("suspicious", confidence=0.61))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("declines to call", why)

    def test_uncertain_is_contested(self):
        tier, _ = classify(verdicted("uncertain", confidence=0.6))
        self.assertEqual(tier, Tier.CONTESTED)

    def test_ai_but_only_suspected_is_contested_not_suspect(self):
        # Certification declined means suspected, not established. Pricing a
        # track as unownable on a suspicion is the expensive mistake.
        tier, why = classify(verdicted("ai", industry_label_status="suspected"))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("certification declined", why.lower())

    def test_low_confidence_human_is_not_counted_clean(self):
        tier, why = classify(verdicted("human", confidence=0.3))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("floor", why)

    def test_an_unknown_verdict_is_never_clean(self):
        tier, why = classify(verdicted("banana"))
        self.assertEqual(tier, Tier.CONTESTED)
        self.assertIn("Not assumed clean", why)

    def test_tier_verdicts_take_precedence_over_verdict(self):
        s = verdicted("human", tier_verdicts={"press_safe": "ai",
                                              "human_safe": "ai",
                                              "recall": "ai"})
        self.assertEqual(classify(s)[0], Tier.SUSPECT)

    def test_label_basis_reaches_the_reason(self):
        _, why = classify(verdicted(
            "suspicious", confidence=0.61,
            industry_label_basis=["Screening threshold cleared; "
                                  "certification threshold not cleared"]))
        self.assertIn("Screening threshold cleared", why)


class TestHeadlineVersusOperatingPoint(unittest.TestCase):
    """Observed live: a real Udio track whose headline verdict said 'human'.

    Its operating points said {press_safe: human, human_safe: ai, recall: ai}.
    Reading only `verdict` would have passed a synthetic recording into the
    acquirable base, which is the exact failure this tool exists to prevent.
    """

    OBSERVED = dict(verdict="human", confidence=0.2939,
                    tier_verdicts={"press_safe": "human", "human_safe": "ai",
                                   "recall": "ai"},
                    origin="human")

    def observed(self):
        return TrackScore(filename="udio.mp3", path="udio.mp3", ai_score=70.6,
                          provider="humanstandard", **self.OBSERVED)

    def test_the_operating_point_wins(self):
        self.assertEqual(classify(self.observed())[0], Tier.SUSPECT)

    def test_the_disagreement_is_stated_not_hidden(self):
        _, why = classify(self.observed())
        self.assertIn("headline verdict", why)
        self.assertIn("human", why)

    def test_agreeing_verdict_adds_no_note(self):
        _, why = classify(TrackScore(
            filename="a.mp3", path="a.mp3", ai_score=99.0, confidence=0.99,
            provider="humanstandard", verdict="ai",
            tier_verdicts={"press_safe": "ai", "human_safe": "ai",
                           "recall": "ai"}))
        self.assertNotIn("headline verdict", why)
