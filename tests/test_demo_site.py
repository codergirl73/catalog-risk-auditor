"""The recorded replay that gets published.

It is the first thing a judge sees, and it claims the events are the agent's
own and the JSON is the API's own. These tests hold it to that: the slides
have to cover every event, and the response shown in full has to be the
response that came back, complete.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "build_demo_site", ROOT / "scripts" / "build_demo_site.py")
build_demo_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_demo_site)

from catalog_audit.agent import PLAN  # noqa: E402
from catalog_audit.models import Asset, AuditResult, Tier, TrackScore  # noqa: E402


def event(kind, title="", detail="", **data):
    return {"type": kind, "title": title, "detail": detail, "data": data}


def recorded():
    """A stream shaped like a real one: plan, then a group per step."""
    events = [event("plan", "Audit plan", "7 steps", steps=list(PLAN))]
    for step in PLAN:
        events.append(event("step", step))
        events.append(event("tool_result", "something", "did a thing"))
    events.append(event("done", "Audit complete", "manifest abc"))
    return events


class TestGrouping(unittest.TestCase):
    def test_the_plan_announcement_gets_its_own_opening_group(self):
        groups = build_demo_site.group_events(recorded())
        self.assertEqual(groups[0]["title"], "")
        self.assertEqual(groups[0]["events"][0]["type"], "plan")

    def test_one_group_per_declared_step(self):
        groups = build_demo_site.group_events(recorded())
        titles = [g["title"] for g in groups if g["title"]]
        self.assertEqual(titles, list(PLAN))

    def test_no_event_is_lost_in_the_grouping(self):
        events = recorded()
        grouped = sum(len(g["events"])
                      for g in build_demo_site.group_events(events))
        self.assertEqual(grouped, len(events))

    def test_trailing_events_stay_with_the_last_step(self):
        groups = build_demo_site.group_events(recorded())
        self.assertEqual(groups[-1]["events"][-1]["type"], "done")

    def test_a_stream_with_no_steps_still_groups(self):
        groups = build_demo_site.group_events([event("plan", "Audit plan")])
        self.assertEqual(len(groups), 1)


class TestSlides(unittest.TestCase):
    def setUp(self):
        self.slides = build_demo_site.build_slides(recorded())

    def test_evidence_follows_the_scoring_step(self):
        kinds = [s["kind"] for s in self.slides]
        titles = [s.get("title") for s in self.slides]
        self.assertEqual(kinds[titles.index(PLAN[1]) + 1], "evidence")

    def test_the_summary_is_last(self):
        self.assertEqual(self.slides[-1]["kind"], "summary")

    def test_every_recorded_event_appears_on_some_slide(self):
        shown = sum(len(s["events"])
                    for s in self.slides if s["kind"] == "events")
        self.assertEqual(shown, len(recorded()))

    def test_slide_count_is_steps_plus_plan_evidence_and_summary(self):
        self.assertEqual(len(self.slides), len(PLAN) + 3)


def asset(name, tier, **kw):
    """One scored asset. Keyword-only beyond the name and tier, because a
    nine-positional-argument fixture is a puzzle at every call site."""
    response = kw.get("response")
    score = TrackScore(
        filename=name, path=name,
        ai_score=kw.get("ai_score", 5.0),
        confidence=kw.get("confidence", 0.9),
        provider="humanstandard",
        verdict=kw.get("verdict", "human"),
        origin=kw.get("origin", ""),
        mock=kw.get("mock", False),
        error=kw.get("error", ""),
        raw={"response": response} if response else {},
        tier_verdicts=(response or {}).get("tier_verdicts", {}),
    )
    return Asset(filename=name, score=score, tier=tier)


def result_with(assets):
    return AuditResult("C", "/c", assets=assets, provider="humanstandard")


class TestTrackRows(unittest.TestCase):
    def test_one_row_per_asset(self):
        rows = build_demo_site.track_rows(result_with([
            asset("a.mp3", Tier.CLEAN), asset("b.mp3", Tier.SUSPECT)]))
        self.assertEqual(len(rows), 2)

    def test_worst_tiers_lead(self):
        rows = build_demo_site.track_rows(result_with([
            asset("clean.mp3", Tier.CLEAN),
            asset("suspect.mp3", Tier.SUSPECT),
            asset("contested.mp3", Tier.CONTESTED)]))
        self.assertEqual([r["tier"] for r in rows],
                         ["suspect", "contested", "clean"])

    def test_a_declined_attribution_is_not_shown_as_a_generator(self):
        for declined in ("human", "uncertain", "UNKNOWN"):
            with self.subTest(origin=declined):
                rows = build_demo_site.track_rows(result_with(
                    [asset("a.mp3", Tier.CLEAN, origin=declined)]))
                self.assertEqual(rows[0]["origin"], "")

    def test_a_real_generator_is_shown(self):
        rows = build_demo_site.track_rows(result_with(
            [asset("a.mp3", Tier.SUSPECT, origin="suno")]))
        self.assertEqual(rows[0]["origin"], "suno")

    def test_a_failed_detection_carries_no_score(self):
        rows = build_demo_site.track_rows(result_with(
            [asset("a.mp3", Tier.ERROR, error="unreachable")]))
        self.assertIsNone(rows[0]["ai_score"])
        self.assertEqual(rows[0]["error"], "unreachable")


AGREES = {"verdict": "ai", "tier_verdicts": {"press_safe": "ai",
                                             "human_safe": "ai",
                                             "recall": "ai"}}
SPLITS = {"verdict": "human", "tier_verdicts": {"press_safe": "human",
                                                "human_safe": "ai",
                                                "recall": "ai"}}


class TestSpotlight(unittest.TestCase):
    def test_a_split_verdict_is_preferred_over_an_agreeing_one(self):
        chosen = build_demo_site.pick_spotlight(result_with([
            asset("agrees.mp3", Tier.SUSPECT, response=AGREES),
            asset("splits.mp3", Tier.SUSPECT, response=SPLITS)]))
        self.assertEqual(chosen["filename"], "splits.mp3")

    def test_the_response_is_carried_through_whole(self):
        chosen = build_demo_site.pick_spotlight(result_with(
            [asset("a.mp3", Tier.SUSPECT, response=SPLITS)]))
        self.assertEqual(chosen["response"], SPLITS)
        self.assertEqual(sorted(chosen["response"]["tier_verdicts"]),
                         ["human_safe", "press_safe", "recall"])

    def test_it_falls_back_to_any_real_response(self):
        chosen = build_demo_site.pick_spotlight(result_with(
            [asset("a.mp3", Tier.SUSPECT, response=AGREES)]))
        self.assertEqual(chosen["filename"], "a.mp3")

    def test_a_mock_response_is_never_shown_as_evidence(self):
        self.assertIsNone(build_demo_site.pick_spotlight(result_with(
            [asset("a.mp3", Tier.SUSPECT, response=AGREES, mock=True)])))

    def test_nothing_to_show_returns_none(self):
        self.assertIsNone(build_demo_site.pick_spotlight(result_with(
            [asset("a.mp3", Tier.CLEAN)])))


if __name__ == "__main__":
    unittest.main()
