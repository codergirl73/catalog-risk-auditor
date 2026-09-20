"""Ground-truth scoring, including the refusal-is-not-an-error rule."""

import unittest

from catalog_audit.evaluation import evaluate, summary
from catalog_audit.models import Asset, Tier


def asset(name, tier, truth):
    return Asset(filename=name, tier=tier, truth=truth)


class TestEvaluate(unittest.TestCase):
    def test_confusion_counts(self):
        assets = [
            asset("tp", Tier.SUSPECT, "ai"),
            asset("fp", Tier.SUSPECT, "human"),
            asset("tn", Tier.CLEAN, "human"),
            asset("fn", Tier.CLEAN, "ai"),
        ]
        ev = evaluate(assets)
        self.assertEqual(ev.true_positive, 1)
        self.assertEqual(ev.false_positive, 1)
        self.assertEqual(ev.true_negative, 1)
        self.assertEqual(ev.false_negative, 1)
        self.assertEqual(ev.false_positive_files, ["fp"])
        self.assertEqual(ev.false_negative_files, ["fn"])
        self.assertAlmostEqual(ev.precision, 0.5)
        self.assertAlmostEqual(ev.recall, 0.5)

    def test_contested_is_counted_separately_not_as_a_miss(self):
        assets = [
            asset("c1", Tier.CONTESTED, "ai"),
            asset("c2", Tier.CONTESTED, "human"),
        ]
        ev = evaluate(assets)
        self.assertEqual(ev.contested_ai, 1)
        self.assertEqual(ev.contested_human, 1)
        self.assertEqual(ev.false_negative, 0)
        self.assertEqual(ev.false_positive, 0)

    def test_unlabelled_assets_are_skipped(self):
        ev = evaluate([asset("x", Tier.SUSPECT, "")])
        self.assertEqual(ev.labelled, 0)

    def test_errors_are_not_scored(self):
        ev = evaluate([asset("e", Tier.ERROR, "ai")])
        self.assertEqual(ev.labelled, 1)
        self.assertEqual(ev.true_positive, 0)
        self.assertEqual(ev.false_negative, 0)

    def test_precision_is_none_with_no_positive_calls(self):
        ev = evaluate([asset("tn", Tier.CLEAN, "human")])
        self.assertIsNone(ev.precision)

    def test_summary_mentions_false_positives_by_name(self):
        ev = evaluate([asset("lofi_demo.mp3", Tier.SUSPECT, "human")])
        self.assertIn("lofi_demo.mp3", summary(ev))

    def test_summary_without_labels(self):
        self.assertIn("No ground-truth", summary(evaluate([])))


if __name__ == "__main__":
    unittest.main()
