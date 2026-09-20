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
