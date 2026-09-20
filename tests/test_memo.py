"""The memo's refusal to launder mock numbers, and its HTML hygiene."""

import unittest

from catalog_audit import memo
from catalog_audit.models import (Asset, AuditResult, Evaluation, Royalty,
                                  Tier, TrackScore, Valuation)


def asset(name, ai, usd, tier, truth=""):
    return Asset(
        filename=name,
        score=TrackScore(filename=name, path=name, ai_score=ai,
                         confidence=0.9, provider="test"),
        royalty=Royalty(filename=name, title=name, artist="A", annual_usd=usd),
        tier=tier, truth=truth, notes=["because"],
    )


def result(mock=False, assets=None):
    assets = assets if assets is not None else [
        asset("clean.mp3", 5, 1000.0, Tier.CLEAN, "human"),
        asset("grey.mp3", 45, 200.0, Tier.CONTESTED, "ai"),
        asset("fake.mp3", 95, 50.0, Tier.SUSPECT, "ai"),
    ]
    r = AuditResult(catalog_name="Test Catalog", catalog_dir="/tmp",
                    assets=assets, provider="test", mock_mode=mock)
    r.valuation = Valuation(
        track_count=len(assets), annual_revenue_usd=1250.0, multiple=15.0,
        asking_price_usd=18750.0, clean_count=1, contested_count=1,
        suspect_count=1, suspect_revenue_usd=50.0, contested_revenue_usd=200.0,
        suspect_revenue_share=0.04, contested_revenue_share=0.16,
        recommended_escrow_usd=2250.0, escrow_basis="basis text",
    )
    r.review_queue = [{"filename": "grey.mp3", "ai_score": 45.0,
                       "annual_usd": 200.0, "reason": "contested band"}]
    r.evaluation = Evaluation(labelled=3, true_positive=1, true_negative=1,
                              contested_ai=1)
    return r


class TestMockGate(unittest.TestCase):
    def test_mock_run_refuses_to_render(self):
        with self.assertRaises(memo.MockModeRefused):
            memo.render(result(mock=True))

    def test_refusal_names_the_override(self):
        try:
            memo.render(result(mock=True))
        except memo.MockModeRefused as exc:
            self.assertIn("--allow-mock", str(exc))

    def test_override_renders_but_marks_the_document(self):
        html = memo.render(result(mock=True), allow_mock=True)
        self.assertIn("MOCK DATA", html)
        self.assertIn("fabricated", html)

    def test_real_run_renders_without_a_banner(self):
        html = memo.render(result(mock=False))
        self.assertNotIn("MOCK DATA", html)


class TestContent(unittest.TestCase):
    def setUp(self):
        self.html = memo.render(result())

    def test_leads_with_the_escrow_figure(self):
        self.assertIn("2,250", self.html)

    def test_reports_both_count_and_revenue_exposure(self):
        # The whole argument of the tool is that these two differ.
        self.assertIn("Revenue on suspect assets", self.html)
        self.assertIn("Suspect", self.html)

    def test_includes_the_review_queue_with_reasons(self):
        self.assertIn("Human review queue", self.html)
        self.assertIn("contested band", self.html)

    def test_states_its_own_accuracy(self):
        self.assertIn("ground truth", self.html.lower())

    def test_states_the_thresholds_it_used(self):
        self.assertIn("clean &lt; 25", self.html)

    def test_carries_the_not_a_legal_opinion_disclaimer(self):
        self.assertIn("not a legal opinion", self.html)

    def test_is_self_contained_html(self):
        # A memo that needs a network to render is useless on a projector.
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertNotIn("<script", self.html.lower())
        self.assertNotIn("http://", self.html)


class TestEscaping(unittest.TestCase):
    def test_hostile_filenames_are_escaped(self):
        bad = asset("<script>alert(1)</script>.mp3", 90, 10.0, Tier.SUSPECT)
        r = result(assets=[bad])
        r.review_queue = [{"filename": "<img onerror=x>", "ai_score": 90.0,
                           "annual_usd": 10.0, "reason": "<b>why</b>"}]
        html = memo.render(r)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img onerror", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
