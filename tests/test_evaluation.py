"""Ground-truth scoring, including the refusal-is-not-an-error rule."""

import unittest

from catalog_audit.evaluation import evaluate, summary
from catalog_audit.models import Asset, Evaluation, Tier


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


class TestSmallSampleHonesty(unittest.TestCase):
    """A rate over a handful of tracks is a count wearing a decimal point."""

    def test_thin_sample_is_flagged_as_indicative(self):
        ev = Evaluation(labelled=50, true_positive=3, true_negative=47)
        text = summary(ev)
        self.assertIn("indicative", text)

    def test_a_healthy_sample_is_not_caveated(self):
        ev = Evaluation(labelled=200, true_positive=40, false_negative=5,
                        true_negative=155)
        self.assertNotIn("indicative", summary(ev))

    def test_the_caveat_names_the_actual_count(self):
        ev = Evaluation(labelled=50, true_positive=1, true_negative=49)
        self.assertIn("Only 1 AI track", summary(ev))

    def test_no_labels_reports_no_accuracy_at_all(self):
        self.assertIn("No ground-truth", summary(Evaluation()))


class TestAttributionAccuracy(unittest.TestCase):
    """Grading not just 'was it AI' but 'did it name the right generator'."""

    @staticmethod
    def ai_asset(name, claimed, actual, tier=Tier.SUSPECT):
        from catalog_audit.models import TrackScore
        return Asset(filename=name,
                     score=TrackScore(name, name, 95.0, 0.9, "hs",
                                      origin=claimed),
                     tier=tier, truth="ai", true_origin=actual)

    def test_correct_attribution_is_counted(self):
        ev = evaluate([self.ai_asset("a.mp3", "suno", "suno")])
        self.assertEqual(ev.origin_correct, 1)
        self.assertEqual(ev.origin_labelled, 1)
        self.assertAlmostEqual(ev.origin_accuracy, 1.0)

    def test_wrong_attribution_is_recorded_by_name(self):
        ev = evaluate([self.ai_asset("b.mp3", "udio", "suno")])
        self.assertEqual(ev.origin_wrong, 1)
        self.assertIn("said udio, was suno", ev.origin_confusions[0])

    def test_missing_attribution_is_its_own_bucket(self):
        ev = evaluate([self.ai_asset("c.mp3", "", "suno")])
        self.assertEqual(ev.origin_absent, 1)
        self.assertEqual(ev.origin_correct, 0)

    def test_human_origin_counts_as_no_attribution(self):
        ev = evaluate([self.ai_asset("d.mp3", "human", "udio")])
        self.assertEqual(ev.origin_absent, 1)

    def test_only_caught_tracks_are_graded_on_attribution(self):
        # A track that was missed cannot have been attributed.
        ev = evaluate([self.ai_asset("e.mp3", "suno", "suno", tier=Tier.CLEAN)])
        self.assertEqual(ev.origin_labelled, 0)

    def test_unlabelled_generator_is_not_graded(self):
        ev = evaluate([self.ai_asset("f.mp3", "suno", "")])
        self.assertEqual(ev.origin_labelled, 0)

    def test_summary_reports_attribution(self):
        ev = evaluate([self.ai_asset("g.mp3", "suno", "suno"),
                       self.ai_asset("h.mp3", "udio", "suno")])
        text = summary(ev)
        self.assertIn("attributed to the right one", text)

    def test_accuracy_is_none_without_labels(self):
        self.assertIsNone(Evaluation().origin_accuracy)


class TestDeclinedAttribution(unittest.TestCase):
    """The API answers 'uncertain' for origin. That is a refusal, not an error."""

    @staticmethod
    def caught(claimed):
        from catalog_audit.models import TrackScore
        return Asset(filename="x.mp3",
                     score=TrackScore("x.mp3", "x.mp3", 99.0, 0.99, "hs",
                                      origin=claimed),
                     tier=Tier.SUSPECT, truth="ai", true_origin="suno")

    def test_uncertain_origin_is_absent_not_wrong(self):
        ev = evaluate([self.caught("uncertain")])
        self.assertEqual(ev.origin_absent, 1)
        self.assertEqual(ev.origin_wrong, 0)

    def test_a_real_misattribution_is_still_wrong(self):
        ev = evaluate([self.caught("udio")])
        self.assertEqual(ev.origin_wrong, 1)
        self.assertEqual(ev.origin_absent, 0)
