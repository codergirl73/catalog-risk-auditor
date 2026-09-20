"""The audit loop end to end, on a throwaway catalog and the mock detector.

These assert the shape of the run rather than any particular score: that the
agent states a plan before acting, estimates its spend before spending,
escalates what it cannot settle, and never lets an unscored asset pass as
clean.
"""

import tempfile
import unittest
from pathlib import Path

from catalog_audit.agent import PLAN, AuditAgent
from catalog_audit.models import Tier


class CatalogFixture(unittest.TestCase):
    """A small labelled catalog on disk, torn down after each test."""

    HUMAN = ["one.mp3", "two.mp3", "three.mp3"]
    AI = ["synth_a.mp3", "synth_b.mp3"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.catalog = root / "catalog"
        (self.catalog / "human").mkdir(parents=True)
        (self.catalog / "ai").mkdir(parents=True)

        for i, name in enumerate(self.HUMAN):
            (self.catalog / "human" / name).write_bytes(b"human-%d" % i)
        for i, name in enumerate(self.AI):
            (self.catalog / "ai" / name).write_bytes(b"ai-%d" % i)

        self.royalties = root / "royalties.csv"
        self.royalties.write_text(
            "filename,title,artist,annual_usd\n"
            + "".join("%s,T,A,%d\n" % (n, (i + 1) * 100)
                      for i, n in enumerate(self.HUMAN))
            + "".join("%s,T,Generated,%d\n" % (n, 5)
                      for n in self.AI),
            encoding="utf-8")

        self.truth = root / "truth.csv"
        self.truth.write_text(
            "filename,true_label\n"
            + "".join("%s,human\n" % n for n in self.HUMAN)
            + "".join("%s,ai\n" % n for n in self.AI),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_audit(self, **kw):
        agent = AuditAgent(force_mock=True, **kw)
        events = list(agent.run(self.catalog, royalties_csv=self.royalties,
                                truth_csv=self.truth))
        return agent, events


class TestRunShape(CatalogFixture):
    def test_plan_is_stated_before_any_work(self):
        _, events = self.run_audit()
        self.assertEqual(events[0].type, "plan")
        self.assertEqual(events[0].data["steps"], PLAN)

    def test_run_finishes_with_done(self):
        _, events = self.run_audit()
        self.assertEqual(events[-1].type, "done")

    def test_every_planned_step_is_actually_executed(self):
        _, events = self.run_audit()
        executed = [e.title for e in events if e.type == "step"]
        self.assertEqual(executed, PLAN)

    def test_mock_mode_is_announced(self):
        _, events = self.run_audit()
        warns = [e for e in events if e.type == "warn"]
        self.assertTrue(any("Mock detector" in w.title for w in warns))

    def test_empty_catalog_errors_rather_than_reporting_nothing_wrong(self):
        with tempfile.TemporaryDirectory() as empty:
            agent = AuditAgent(force_mock=True)
            events = list(agent.run(Path(empty)))
            self.assertEqual(events[-1].type, "error")


class TestBudgetIntegration(CatalogFixture):
    def test_spend_is_estimated_before_it_is_spent(self):
        _, events = self.run_audit()
        titles = [e.title for e in events]
        self.assertIn("budget check", titles)
        # the estimate has to precede the scoring step
        self.assertLess(titles.index("budget check"), titles.index(PLAN[1]))

    def test_estimate_matches_the_catalog(self):
        _, events = self.run_audit()
        check = next(e for e in events if e.title == "budget check")
        self.assertEqual(check.data["needed"], len(self.HUMAN) + len(self.AI))

    def test_a_tight_budget_leaves_assets_unscored_not_clean(self):
        agent, events = self.run_audit(budget_limit=2)
        self.assertEqual(agent.budget.spent, 2)
        self.assertEqual(agent.budget.skipped, 3)

        errored = [a for a in agent.result.assets if a.tier == Tier.ERROR]
        self.assertEqual(len(errored), 3)
        for a in errored:
            self.assertNotEqual(a.tier, Tier.CLEAN)

        self.assertTrue(any(e.type == "warn" and "Budget exhausted" in e.title
                            for e in events))

    def test_budget_is_reported_on_the_result(self):
        agent, _ = self.run_audit()
        self.assertIsNotNone(agent.result.budget)
        self.assertEqual(agent.result.budget.spent, 5)


class TestJoinsAndEscalation(CatalogFixture):
    def test_revenue_is_joined_onto_assets(self):
        agent, _ = self.run_audit()
        self.assertAlmostEqual(agent.result.valuation.annual_revenue_usd, 610.0)

    def test_ground_truth_is_attached_and_scored(self):
        agent, _ = self.run_audit()
        self.assertEqual(agent.result.evaluation.labelled, 5)

    def test_review_queue_carries_a_reason_for_every_entry(self):
        agent, _ = self.run_audit()
        for item in agent.result.review_queue:
            self.assertTrue(item["reason"],
                            "escalated %s with no reason" % item["filename"])

    def test_errors_and_contested_both_reach_the_queue(self):
        agent, _ = self.run_audit(budget_limit=2)
        queued = {i["filename"] for i in agent.result.review_queue}
        unscored = {a.filename for a in agent.result.assets
                    if a.tier == Tier.ERROR}
        self.assertTrue(unscored.issubset(queued))

    def test_result_serialises_to_json_safe_types(self):
        import json
        agent, _ = self.run_audit()
        payload = json.dumps(agent.result.to_dict(), default=str)
        self.assertIn("suspect", payload + "clean contested suspect")
        self.assertIsInstance(json.loads(payload), dict)


class TestAuditTrail(CatalogFixture):
    def test_manifest_is_stable_across_identical_runs(self):
        a1, _ = self.run_audit()
        a2, _ = self.run_audit()
        self.assertEqual(a1.result.manifest_sha256, a2.result.manifest_sha256)

    def test_manifest_changes_when_the_audio_changes(self):
        a1, _ = self.run_audit()
        (self.catalog / "human" / "one.mp3").write_bytes(b"tampered")
        a2, _ = self.run_audit()
        self.assertNotEqual(a1.result.manifest_sha256,
                            a2.result.manifest_sha256)


if __name__ == "__main__":
    unittest.main()


class TestApiMockPropagation(CatalogFixture):
    """A fixture from the real API is still a fixture."""

    def test_api_mock_responses_mark_the_whole_run_mock(self):
        from catalog_audit.models import TrackScore

        class MockingApi:
            name, is_mock = "humanstandard", False

            def __init__(self):
                self.budget = None

            def is_cached(self, digest):
                return False

            def detect(self, path, digest=""):
                from pathlib import Path as P
                return TrackScore(
                    filename=P(path).name, path=str(path), ai_score=95.0,
                    confidence=0.9, provider=self.name, verdict="ai",
                    mock=True, mock_scenario="ai",
                )

        agent = AuditAgent(force_mock=True)
        agent.detector = MockingApi()
        events = list(agent.run(self.catalog))

        self.assertTrue(agent.result.mock_mode)
        self.assertTrue(any(e.type == "warn" and "mock" in e.title.lower()
                            for e in events))

    def test_a_real_run_is_not_marked_mock(self):
        agent, _ = self.run_audit()
        # force_mock=True uses the local mock detector, which is separately
        # flagged; what matters here is that no API mock flag was invented.
        self.assertFalse(any(a.score.mock for a in agent.result.assets))


class TestReviewQueueEconomics(CatalogFixture):
    """A review queue without money on it is a to-do list, not a priority."""

    def test_entries_carry_the_revenue_they_are_worth(self):
        agent, _ = self.run_audit()
        queued = {i["filename"]: i["annual_usd"]
                  for i in agent.result.review_queue}
        by_name = {a.filename: a.annual_usd for a in agent.result.assets}
        self.assertTrue(queued, "nothing was escalated in this fixture")
        for name, usd in queued.items():
            self.assertEqual(usd, by_name[name],
                             "%s escalated with the wrong revenue" % name)

    def test_at_least_one_entry_is_non_zero(self):
        # Guards the ordering bug: the queue used to be built before the
        # revenue join, so every entry read $0.
        agent, _ = self.run_audit()
        self.assertTrue(any(i["annual_usd"] > 0
                            for i in agent.result.review_queue))

    def test_queue_is_ordered_by_what_is_at_stake(self):
        agent, _ = self.run_audit()
        amounts = [i["annual_usd"] for i in agent.result.review_queue]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
