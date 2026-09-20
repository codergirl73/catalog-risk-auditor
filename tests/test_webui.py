"""The local web UI.

It exists to make a run legible from the back of a room, which means it is
also a small HTTP server on a developer's machine -- so what it refuses to do
matters as much as what it renders.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from catalog_audit import webui
from catalog_audit.models import AuditResult, Valuation


def summary_for(**kw):
    defaults = {"track_count": 16, "annual_revenue_usd": 48000.0,
                "multiple": 15.0, "asking_price_usd": 720000.0,
                "clean_count": 8, "contested_count": 2, "suspect_count": 6,
                "error_count": 0, "suspect_revenue_usd": 8365.0,
                "contested_revenue_usd": 6166.0,
                "suspect_revenue_share": 0.174,
                "recommended_escrow_usd": 171710.0}
    defaults.update(kw)
    result = AuditResult("C", "/c", provider="hs")
    result.valuation = Valuation(**defaults)
    return webui._summary(result)


class TestSummary(unittest.TestCase):
    def test_revenue_splits_add_up_to_the_total(self):
        # Reconstructing clean revenue in the browser from shares meant a
        # missing field silently folded contested into clean.
        s = summary_for()
        total = s["clean_revenue"] + s["contested_revenue"] + s["suspect_revenue"]
        self.assertAlmostEqual(total, s["revenue"], places=2)

    def test_the_three_revenue_figures_are_sent_outright(self):
        s = summary_for()
        for key in ("clean_revenue", "contested_revenue", "suspect_revenue"):
            self.assertIn(key, s)

    def test_clean_revenue_never_goes_negative(self):
        s = summary_for(suspect_revenue_usd=40000.0,
                        contested_revenue_usd=40000.0)
        self.assertGreaterEqual(s["clean_revenue"], 0.0)

    def test_an_empty_result_summarises_to_nothing(self):
        self.assertEqual(webui._summary(None), {})
        self.assertEqual(webui._summary(AuditResult("C", "/c")), {})

    def test_the_review_queue_is_capped(self):
        result = AuditResult("C", "/c", provider="hs")
        result.valuation = Valuation(track_count=1, annual_revenue_usd=1.0,
                                     multiple=1.0, asking_price_usd=1.0)
        result.review_queue = [{"filename": "f%d" % i} for i in range(50)]
        self.assertEqual(len(webui._summary(result)["review_queue"]), 10)

    def test_a_mock_run_is_flagged_to_the_page(self):
        result = AuditResult("C", "/c", provider="hs", mock_mode=True)
        result.valuation = Valuation(track_count=1, annual_revenue_usd=1.0,
                                     multiple=1.0, asking_price_usd=1.0)
        self.assertTrue(webui._summary(result)["mock"])


class TestServedRoutes(unittest.TestCase):
    """A live server on an ephemeral port, bound to localhost."""

    @classmethod
    def setUpClass(cls):
        options = {"catalog": ".", "royalties": None, "truth": None,
                   "multiple": None, "asking_price": None, "name": None,
                   "mock": True, "allow_mock": False, "budget": 0}
        cls.httpd = webui.AuditServer((webui.HOST, 0), webui.Handler, options)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        url = "http://%s:%d%s" % (webui.HOST, self.port, path)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers or {})

    def test_the_page_and_its_assets_are_served(self):
        for path, marker in (("/", b"Catalog Risk Auditor"),
                             ("/app.css", b"--suspect"),
                             ("/app.js", b"EventSource")):
            with self.subTest(path=path):
                status, body, _ = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn(marker, body)

    def test_an_unknown_route_is_not_found(self):
        self.assertEqual(self.get("/nope")[0], 404)

    def test_there_is_no_static_file_handler_to_traverse(self):
        for path in ("/../catalog_audit/config.py", "/etc/passwd",
                     "/..%2f..%2f.env", "/.env"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_every_response_carries_a_content_security_policy(self):
        _, _, headers = self.get("/")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_the_memo_route_reports_that_nothing_has_run(self):
        status, body, _ = self.get("/memo")
        self.assertEqual(status, 404)
        self.assertIn(b"No audit", body)

    def test_the_page_loads_no_third_party_anything(self):
        _, body, _ = self.get("/")
        text = body.decode()
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)


class TestBinding(unittest.TestCase):
    def test_it_binds_to_loopback_only(self):
        # An audit pane showing somebody's catalog and its valuation is not a
        # page to put on a LAN by accident.
        self.assertEqual(webui.HOST, "127.0.0.1")

    def test_the_server_is_threaded_so_a_stream_cannot_block_the_page(self):
        self.assertTrue(issubclass(webui.AuditServer, ThreadingHTTPServer))

    def test_only_one_audit_runs_at_a_time(self):
        self.assertTrue(webui._RUNNING.acquire(blocking=False))
        try:
            self.assertFalse(webui._RUNNING.acquire(blocking=False))
        finally:
            webui._RUNNING.release()


class TestEventEncoding(unittest.TestCase):
    def test_frames_are_well_formed_server_sent_events(self):
        frame = webui._sse("step", {"title": "x"}).decode()
        self.assertTrue(frame.startswith("event: step\ndata: "))
        self.assertTrue(frame.endswith("\n\n"))
        self.assertEqual(json.loads(frame.split("data: ")[1])["title"], "x")

    def test_a_newline_in_a_payload_cannot_break_the_frame(self):
        frame = webui._sse("warn", {"detail": "line one\nline two"}).decode()
        self.assertEqual(len([line for line in frame.splitlines() if line]), 2)


if __name__ == "__main__":
    unittest.main()


class TestStreamedRun(unittest.TestCase):
    """The stream is the UI. Everything else on the page is decoration."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        cls.tmp = tempfile.TemporaryDirectory()
        catalog = Path(cls.tmp.name) / "catalog"
        catalog.mkdir()
        for i in range(3):
            (catalog / ("t%d.mp3" % i)).write_bytes(b"audio-%d" % i)

        options = {"catalog": catalog, "royalties": None, "truth": None,
                   "multiple": None, "asking_price": None, "name": "Demo",
                   "mock": True, "allow_mock": True, "budget": None}
        cls.httpd = webui.AuditServer((webui.HOST, 0), webui.Handler, options)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def stream(self):
        url = "http://%s:%d/events" % (webui.HOST, self.port)
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read().decode()

    def events(self, body):
        out = []
        for block in body.split("\n\n"):
            if not block.strip():
                continue
            kind = block.splitlines()[0].replace("event: ", "")
            payload = json.loads(block.split("data: ", 1)[1])
            out.append((kind, payload))
        return out

    def test_a_run_streams_a_plan_first_and_a_summary_last(self):
        events = self.events(self.stream())
        self.assertEqual(events[0][0], "plan")
        self.assertEqual(events[-1][0], "summary")

    def test_the_plan_arrives_once_with_its_steps(self):
        # It used to be sent twice, in two different shapes.
        events = self.events(self.stream())
        plans = [p for kind, p in events if kind == "plan"]
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0]["data"]["steps"])

    def test_the_summary_carries_the_numbers_the_page_shows(self):
        events = self.events(self.stream())
        summary = events[-1][1]
        for key in ("escrow", "tracks", "clean", "contested", "suspect",
                    "clean_revenue", "contested_revenue", "suspect_revenue"):
            self.assertIn(key, summary)

    def test_a_mock_run_is_marked_so_the_page_withholds_the_memo(self):
        summary = self.events(self.stream())[-1][1]
        self.assertTrue(summary["mock"])

    def test_the_memo_route_refuses_a_mock_run_in_words(self):
        self.stream()
        url = "http://%s:%d/memo" % (webui.HOST, self.port)
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
        self.assertIn("MOCK DATA", body)

    def test_a_second_concurrent_run_is_refused_not_queued(self):
        held = webui._RUNNING.acquire(blocking=False)
        self.assertTrue(held)
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.stream()
            self.assertEqual(ctx.exception.code, 409)
        finally:
            webui._RUNNING.release()
