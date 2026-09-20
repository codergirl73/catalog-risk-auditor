"""Response mapping, the call budget, and the upload guard.

map_response() is the one function that has to survive meeting a schema
nobody documented, so it is tested against every response shape it claims to
handle -- and against one it does not, because failing loudly beats inventing
a score.
"""

import json
import tempfile
import unittest
from pathlib import Path

from catalog_audit import config
from catalog_audit.detector import (LiveDetector, MockDetector, get_detector,
                                    map_response, parse_result, sha256_file)
from catalog_audit.models import Budget


class TestMapResponse(unittest.TestCase):
    def test_direct_ai_score_percentage(self):
        ai, conf = map_response({"ai_score": 91.4, "confidence": 0.88})
        self.assertAlmostEqual(ai, 91.4)
        self.assertAlmostEqual(conf, 0.88)

    def test_camel_case_key(self):
        ai, _ = map_response({"aiScore": 72})
        self.assertAlmostEqual(ai, 72.0)

    def test_zero_to_one_score_is_rescaled(self):
        ai, _ = map_response({"ai_probability": 0.93})
        self.assertAlmostEqual(ai, 93.0)

    def test_human_score_is_inverted(self):
        ai, _ = map_response({"human_score": 0.95})
        self.assertAlmostEqual(ai, 5.0)

    def test_human_score_as_percentage_is_inverted(self):
        ai, _ = map_response({"humanScore": 80})
        self.assertAlmostEqual(ai, 20.0)

    def test_nested_payload_is_flattened(self):
        ai, conf = map_response({"result": {"ai_score": 40, "confidence": 0.7}})
        self.assertAlmostEqual(ai, 40.0)
        self.assertAlmostEqual(conf, 0.7)

    def test_label_synthetic(self):
        ai, _ = map_response({"label": "synthetic"})
        self.assertGreater(ai, config.SUSPECT_FLOOR)

    def test_label_human(self):
        ai, _ = map_response({"classification": "human"})
        self.assertLess(ai, config.CLEAN_CEILING)

    def test_label_hybrid_lands_in_the_contested_band(self):
        # HumanStandard describes synthetic/human/hybrid classes. A hybrid
        # verdict is exactly the case the contested band exists for, so it
        # must not fall out on either side of it.
        ai, _ = map_response({"label": "hybrid"})
        self.assertGreater(ai, config.CLEAN_CEILING)
        self.assertLess(ai, config.SUSPECT_FLOOR)

    def test_confidence_percentage_is_normalised(self):
        _, conf = map_response({"ai_score": 50, "confidence": 85})
        self.assertAlmostEqual(conf, 0.85)

    def test_missing_confidence_gets_a_default(self):
        _, conf = map_response({"ai_score": 50})
        self.assertGreater(conf, 0.0)
        self.assertLessEqual(conf, 1.0)

    def test_scores_are_clamped(self):
        ai, _ = map_response({"ai_score": 140})
        self.assertEqual(ai, 100.0)

    def test_booleans_are_not_mistaken_for_scores(self):
        # bool is a subclass of int; {"score": True} must not become 100.0.
        with self.assertRaises(ValueError):
            map_response({"score": True, "detected": False})

    def test_unrecognised_schema_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError) as ctx:
            map_response({"totally": "unexpected"})
        # The message has to tell whoever is debugging where to look.
        self.assertIn("map_response", str(ctx.exception))
        self.assertIn("totally", str(ctx.exception))


class TestBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audio = Path(self.tmp.name) / "track.mp3"
        self.audio.write_bytes(b"not really audio, but it hashes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_budget_stops_spending_at_the_limit(self):
        b = Budget(limit=2)
        det = MockDetector(budget=b)
        results = [det.detect(self.audio) for _ in range(4)]
        self.assertEqual(b.spent, 2)
        self.assertEqual(b.skipped, 2)
        self.assertTrue(all(r.ok for r in results[:2]))
        self.assertTrue(all(not r.ok for r in results[2:]))

    def test_unscored_assets_are_errors_not_clean(self):
        from catalog_audit.tiering import classify
        from catalog_audit.models import Tier
        b = Budget(limit=0)
        result = MockDetector(budget=b).detect(self.audio)
        tier, reason = classify(result)
        self.assertEqual(tier, Tier.ERROR)
        self.assertIn("budget", reason.lower())

    def test_remaining_never_goes_negative(self):
        b = Budget(limit=1)
        b.spend()
        b.spend()
        self.assertEqual(b.remaining, 0)
        self.assertTrue(b.exhausted)

    def test_detector_runs_unbudgeted_when_none_given(self):
        det = MockDetector()
        self.assertTrue(det.detect(self.audio).ok)


class TestUploadGuard(unittest.TestCase):
    def test_oversized_file_is_refused_without_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / "dj_set.mp3"
            big.write_bytes(b"\0" * int(config.HS_MAX_UPLOAD_MB * 1e6 + 1024))
            msg = LiveDetector._oversized(big)
            self.assertIn("upload ceiling", msg)

    def test_normal_file_passes_the_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = Path(tmp) / "track.mp3"
            ok.write_bytes(b"\0" * 1024)
            self.assertEqual(LiveDetector._oversized(ok), "")


class TestHashAndSelection(unittest.TestCase):
    def test_hash_is_stable_and_content_addressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.mp3", Path(tmp) / "b.mp3"
            a.write_bytes(b"same bytes")
            b.write_bytes(b"same bytes")
            self.assertEqual(sha256_file(a), sha256_file(b))

    def test_mock_is_selected_when_no_key_is_configured(self):
        original = config.HS_API_KEY
        try:
            config.HS_API_KEY = ""
            self.assertTrue(get_detector().is_mock)
        finally:
            config.HS_API_KEY = original

    def test_mock_is_flagged_as_mock(self):
        self.assertTrue(MockDetector().is_mock)
        self.assertFalse(LiveDetector.is_mock)


if __name__ == "__main__":
    unittest.main()


class TestRealSchema(unittest.TestCase):
    """The documented HumanStandard response, as published at docs.hsverify.com."""

    REAL = {
        "verdict": "ai",
        "confidence": 0.91,
        "origin": "suno",
        "origin_confidence": 0.82,
        "risk_timeline": [0.12, 0.14, 0.88, 0.91, 0.89, 0.87],
        "origin_map_evidence": "https://example.com/map.png",
        "origin_map": {
            "nearest_population": "Suno",
            "summary_line": "24 of its 25 nearest reference recordings are "
                            "Suno generations.",
            "neighborhood": {"k": 25, "dominant": True,
                             "counts": {"Suno": 24, "Udio": 1}},
        },
        "headline_verdict": "ai_generated",
        "industry_label": "AI-Generated",
        "industry_label_status": "meets_definition",
        "tier_verdicts": {"press_safe": "ai", "human_safe": "ai",
                          "recall": "ai"},
        "duration_sec": 187.4,
        "model_version": "2026-04-29",
        "processed_at": "2026-04-29T10:43:01Z",
    }

    def test_ai_verdict_scores_high(self):
        ai, conf = map_response(self.REAL)
        self.assertGreater(ai, config.SUSPECT_FLOOR)
        self.assertAlmostEqual(conf, 0.91)

    def test_human_verdict_scores_low(self):
        ai, _ = map_response({"verdict": "human", "confidence": 0.95})
        self.assertLess(ai, config.CLEAN_CEILING)

    def test_uncertain_verdict_sits_at_the_midpoint(self):
        # The detector declining to call it must not be rounded to either side.
        ai, _ = map_response({"verdict": "uncertain", "confidence": 0.55})
        self.assertGreater(ai, config.CLEAN_CEILING)
        self.assertLess(ai, config.SUSPECT_FLOOR)

    def test_confidence_moves_the_score_away_from_the_midpoint(self):
        low, _ = map_response({"verdict": "ai", "confidence": 0.55})
        high, _ = map_response({"verdict": "ai", "confidence": 0.99})
        self.assertLess(low, high)

    def test_every_evidence_field_is_extracted(self):
        got = parse_result(self.REAL)
        self.assertEqual(got["origin"], "suno")
        self.assertEqual(got["industry_label"], "AI-Generated")
        self.assertEqual(got["tier_verdicts"]["human_safe"], "ai")
        self.assertIn("Suno generations", got["origin_summary"])
        self.assertEqual(got["origin_map_evidence"],
                         "https://example.com/map.png")
        self.assertEqual(len(got["risk_timeline"]), 6)
        self.assertAlmostEqual(got["duration_s"], 187.4)

    def test_result_may_be_nested_under_result(self):
        got = parse_result({"status": "complete", "result": self.REAL})
        self.assertEqual(got["verdict"], "ai")

    def test_a_sparse_response_still_parses(self):
        got = parse_result({"verdict": "human", "confidence": 0.9})
        self.assertEqual(got["verdict"], "human")
        self.assertEqual(got["origin"], "")
        self.assertEqual(got["tier_verdicts"], {})

    def test_api_mock_flag_is_carried_through(self):
        got = parse_result({**self.REAL, "mock": True,
                            "mock_scenario": "ai"})
        self.assertTrue(got["mock"])
        self.assertEqual(got["mock_scenario"], "ai")

    def test_a_real_response_is_not_flagged_as_mock(self):
        self.assertFalse(parse_result(self.REAL)["mock"])


class TestUrls(unittest.TestCase):
    def test_status_url_interpolates_the_job_id(self):
        det = LiveDetector.__new__(LiveDetector)
        url = det._status_request("job-abc").full_url
        self.assertIn("/api/jobs/job-abc/status", url)

    def test_detail_full_is_requested_by_default(self):
        det = LiveDetector.__new__(LiveDetector)
        self.assertIn("detail=full", det._query())

    def test_mock_scenario_reaches_the_query_string(self):
        original = config.HS_MOCK_SCENARIO
        try:
            config.HS_MOCK_SCENARIO = "suspicious"
            det = LiveDetector.__new__(LiveDetector)
            self.assertIn("mock=suspicious", det._query())
        finally:
            config.HS_MOCK_SCENARIO = original


class TestLiveResponseShapes(unittest.TestCase):
    """Captured from the live API, which differs from the published docs."""

    HUMAN = {"verdict": "human", "confidence": 0.9841, "ai_probability": 0.0159,
             "origin": None, "origin_confidence": None, "duration_sec": 213.44,
             "model_version": "hsv-1.3.0", "risk_timeline": [0.02, 0.01],
             "mock": True, "mock_scenario": "human"}

    AI = {"verdict": "ai", "confidence": 0.9816, "ai_probability": 0.9816,
          "origin": "suno", "origin_confidence": 0.87,
          "industry_label": "ai_generated",
          "industry_label_status": "meets_definition",
          "industry_label_basis": ["Full-mix certification threshold cleared"],
          "risk_timeline": [0.94, 0.97], "mock": True, "mock_scenario": "ai"}

    SUSPICIOUS = {"verdict": "suspicious", "confidence": 0.612,
                  "ai_probability": 0.612, "origin": None,
                  "industry_label": "ai_generated",
                  "industry_label_status": "suspected",
                  "industry_label_basis": ["Screening threshold cleared; "
                                           "certification threshold not cleared"],
                  "mock": True, "mock_scenario": "suspicious"}

    def test_ai_probability_drives_the_score(self):
        self.assertAlmostEqual(map_response(self.HUMAN)[0], 1.6, places=1)
        self.assertAlmostEqual(map_response(self.AI)[0], 98.2, places=1)
        self.assertAlmostEqual(map_response(self.SUSPICIOUS)[0], 61.2, places=1)

    def test_ai_probability_is_preferred_over_verdict_reconstruction(self):
        # verdict+confidence would give 50 + 0.9816*50 = 99.1; the direct
        # probability is 98.2 and is the number to trust.
        self.assertAlmostEqual(map_response(self.AI)[0], 98.2, places=1)

    def test_suspicious_verdict_is_handled(self):
        ai, conf = map_response(self.SUSPICIOUS)
        self.assertGreater(ai, config.CLEAN_CEILING)
        self.assertAlmostEqual(conf, 0.612)

    def test_snake_case_industry_label_is_normalised_for_display(self):
        self.assertEqual(parse_result(self.AI)["industry_label"],
                         "AI-Generated")

    def test_label_basis_array_is_captured(self):
        got = parse_result(self.SUSPICIOUS)["industry_label_basis"]
        self.assertEqual(len(got), 1)
        self.assertIn("Screening threshold", got[0])

    def test_null_origin_does_not_crash(self):
        self.assertEqual(parse_result(self.HUMAN)["origin"], "")

    def test_missing_tier_verdicts_is_not_an_error(self):
        self.assertEqual(parse_result(self.AI)["tier_verdicts"], {})

    def test_every_live_fixture_is_flagged_as_mock(self):
        for payload in (self.HUMAN, self.AI, self.SUSPICIOUS):
            self.assertTrue(parse_result(payload)["mock"])


class TestRiskSegments(unittest.TestCase):
    """The live API sends risk_segments_full_mix, not risk_timeline."""

    LIVE = {"verdict": "human", "confidence": 0.9914, "ai_probability": 0.0086,
            "origin": "human", "origin_confidence": 0.6,
            "risk_segments_full_mix": [
                {"start": 0.0, "end": 20.0, "risk": 0.0},
                {"start": 20.0, "end": 22.5, "risk": 0.4671},
                {"start": 22.5, "end": 31.25, "risk": 0.0}],
            "risk_segments_vocal": [], "risk_segments_instrumental": [],
            "tier_verdicts": {"press_safe": "human", "human_safe": "human",
                              "recall": "human"},
            "model_version": "hsv-1.3.1"}

    def test_full_mix_segments_are_read(self):
        got = parse_result(self.LIVE)
        self.assertEqual(len(got["risk_segments"]), 3)
        self.assertAlmostEqual(got["risk_segments"][1]["start"], 20.0)

    def test_timeline_is_derived_when_absent(self):
        self.assertEqual(len(parse_result(self.LIVE)["risk_timeline"]), 3)

    def test_empty_stem_lists_are_not_preferred_over_the_full_mix(self):
        self.assertEqual(len(parse_result(self.LIVE)["risk_segments"]), 3)

    def test_peak_uses_real_timestamps(self):
        from catalog_audit.models import TrackScore
        sc = TrackScore("x", "x", 0.9, 0.99, "hs", **parse_result(self.LIVE))
        at, risk = sc.peak_risk
        self.assertAlmostEqual(at, 20.0)
        self.assertAlmostEqual(risk, 0.4671)

    def test_real_tier_verdicts_drive_tiering(self):
        from catalog_audit.models import TrackScore
        from catalog_audit.tiering import classify
        from catalog_audit.models import Tier
        sc = TrackScore("x", "x", 0.9, 0.99, "hs", **parse_result(self.LIVE))
        tier, why = classify(sc)
        self.assertEqual(tier, Tier.CLEAN)
        self.assertIn("every operating point", why)

    def test_human_origin_is_not_reported_as_an_attribution(self):
        from catalog_audit.models import TrackScore
        from catalog_audit.tiering import _evidence
        sc = TrackScore("x", "x", 0.9, 0.99, "hs", **parse_result(self.LIVE))
        self.assertNotIn("Attributed to Human", _evidence(sc))
