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
                                    map_response, sha256_file)
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
