"""Submit-and-poll, retries and rate limiting, with no network involved.

The transport is the part that cost the most to get wrong -- the endpoint is
asynchronous and the first client assumed it was not -- so it is exercised
against a fake that answers like the documented API.
"""

from unittest import TestCase, main, mock

from catalog_audit import config
from catalog_audit.detector import LiveDetector


def detector():
    """A LiveDetector with no cache directory and no throttle delay."""
    det = LiveDetector.__new__(LiveDetector)
    det._last_call = 0.0
    det.budget = None
    return det


class TestRequestShape(TestCase):
    def setUp(self):
        self._key = config.HS_API_KEY
        config.HS_API_KEY = "test-key-123"

    def tearDown(self):
        config.HS_API_KEY = self._key

    def test_analyze_url_carries_detail_full(self):
        self.assertIn("detail=full", detector()._query())

    def test_status_url_interpolates_and_escapes_the_job_id(self):
        url = detector()._status_request("job/../etc").full_url
        self.assertNotIn("/../", url)
        self.assertIn("/api/jobs/", url)

    def test_auth_header_follows_the_configured_style(self):
        original = config.HS_AUTH_STYLE
        try:
            config.HS_AUTH_STYLE = "bearer"
            self.assertEqual(LiveDetector.auth_headers(),
                             {"Authorization": "Bearer test-key-123"})
            config.HS_AUTH_STYLE = "x-api-key"
            self.assertEqual(LiveDetector.auth_headers(),
                             {"X-API-Key": "test-key-123"})
        finally:
            config.HS_AUTH_STYLE = original

    def test_every_request_identifies_the_client(self):
        # Cloudflare answers urllib's default agent with a 403 that reads
        # exactly like a rejected key. The User-Agent is not decoration.
        headers = LiveDetector.base_headers()
        self.assertIn("User-Agent", headers)
        self.assertIn("catalog-risk-auditor", headers["User-Agent"])


class TestPolling(TestCase):
    def setUp(self):
        self._interval = config.HS_POLL_INTERVAL_S
        self._timeout = config.HS_POLL_TIMEOUT_S
        config.HS_POLL_INTERVAL_S = 0.0

    def tearDown(self):
        config.HS_POLL_INTERVAL_S = self._interval
        config.HS_POLL_TIMEOUT_S = self._timeout

    def test_polls_until_complete_then_returns_the_result(self):
        det = detector()
        answers = [
            {"status": "queued"},
            {"status": "running"},
            {"status": "complete", "result": {"verdict": "ai"}},
        ]
        with mock.patch.object(det, "_fetch", side_effect=answers):
            self.assertEqual(det._poll("job-1"), {"verdict": "ai"})

    def test_a_failed_job_raises_with_the_reason(self):
        det = detector()
        with mock.patch.object(
                det, "_fetch",
                return_value={"status": "failed", "error": "bad audio"}), \
                self.assertRaises(RuntimeError) as ctx:
            det._poll("job-2")
        self.assertIn("bad audio", str(ctx.exception))

    def test_a_job_that_never_finishes_times_out(self):
        config.HS_POLL_TIMEOUT_S = 0.05
        det = detector()
        with mock.patch.object(det, "_fetch",
                               return_value={"status": "running"}), \
                self.assertRaises(RuntimeError) as ctx:
            det._poll("job-3")
        self.assertIn("did not complete", str(ctx.exception))

    def test_a_status_payload_without_result_is_returned_whole(self):
        det = detector()
        with mock.patch.object(det, "_fetch",
                               return_value={"status": "complete",
                                             "verdict": "human"}):
            self.assertEqual(det._poll("job-4")["verdict"], "human")


class TestCallFlow(TestCase):
    def setUp(self):
        config.HS_POLL_INTERVAL_S = 0.0

    def test_submit_returns_a_job_that_is_then_polled(self):
        det = detector()
        with mock.patch.object(det, "_build_request", return_value=object()), \
             mock.patch.object(det, "_fetch", side_effect=[
                 {"job_id": "abc"},
                 {"status": "complete", "result": {"verdict": "ai"}}]):
            self.assertEqual(det._call(__file__), {"verdict": "ai"})

    def test_a_synchronous_verdict_is_accepted_without_a_job_id(self):
        # The hybrid endpoint answers directly; insisting on a job id would
        # reject a perfectly good verdict.
        det = detector()
        with mock.patch.object(det, "_build_request", return_value=object()), \
             mock.patch.object(det, "_fetch",
                               return_value={"verdict": "human",
                                             "confidence": 0.9}):
            self.assertEqual(det._call(__file__)["verdict"], "human")

    def test_a_response_with_neither_job_nor_verdict_raises(self):
        det = detector()
        with mock.patch.object(det, "_build_request", return_value=object()), \
             mock.patch.object(det, "_fetch",
                               return_value={"unexpected": 1}), \
             self.assertRaises(RuntimeError) as ctx:
            det._call(__file__)
        self.assertIn("unexpected", str(ctx.exception))


class TestRetries(TestCase):
    def test_a_rejected_request_is_not_retried(self):
        """400/401/403/415/422 mean the server decided on the merits."""
        det = detector()
        for code in (" 400:", " 401:", " 403:", " 415:", " 422:"):
            with self.subTest(code=code):
                call = mock.Mock(side_effect=RuntimeError("API%s nope" % code))
                with mock.patch.object(det, "_call", call), \
                        self.assertRaises(RuntimeError):
                    det._call_with_retries(__file__)
                self.assertEqual(call.call_count, 1)

    def test_a_transient_failure_is_retried_then_succeeds(self):
        det = detector()
        call = mock.Mock(side_effect=[RuntimeError("API 503: later"),
                                      {"verdict": "ai"}])
        with mock.patch.object(det, "_call", call), \
             mock.patch("time.sleep"):
            self.assertEqual(det._call_with_retries(__file__),
                             {"verdict": "ai"})
        self.assertEqual(call.call_count, 2)

    def test_retries_are_bounded(self):
        det = detector()
        call = mock.Mock(side_effect=RuntimeError("API 500: boom"))
        with mock.patch.object(det, "_call", call), \
                mock.patch("time.sleep"), \
                self.assertRaises(RuntimeError):
            det._call_with_retries(__file__)
        self.assertEqual(call.call_count, config.HS_MAX_RETRIES + 1)


class TestThrottle(TestCase):
    def test_requests_are_spaced_by_the_configured_gap(self):
        det = detector()
        original = config.HS_RATE_LIMIT_S
        try:
            config.HS_RATE_LIMIT_S = 5.0
            # _throttle reads the clock twice: once to size the wait, once to
            # record when this request went out.
            clock = [100.0, 100.0, 100.5, 105.5]
            with mock.patch("time.time", side_effect=clock), \
                 mock.patch("time.sleep") as slept:
                det._last_call = 0.0   # long enough ago to owe nothing
                det._throttle()      # first call goes straight out
                det._throttle()      # second: only 0.5s has passed of 5.0
            # only the second call waits, and only for the remainder
            slept.assert_called_once()
            self.assertAlmostEqual(slept.call_args[0][0], 4.5, places=1)
        finally:
            config.HS_RATE_LIMIT_S = original


if __name__ == "__main__":
    main()
