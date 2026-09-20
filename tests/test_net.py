"""The one place in the project that opens a socket.

urlopen is a URL opener, not a web client: left unchecked it will read
file:///etc/passwd and hand back the contents as though a server had sent
them. Every outbound request goes through this module so that check happens
once rather than being trusted in four places.
"""

import unittest
import urllib.request

from catalog_audit.net import ALLOWED_SCHEMES, UnsafeURLError, check_url


class TestSchemeGuard(unittest.TestCase):
    def test_https_is_allowed(self):
        url = "https://app.jobsbyhumans.com/api/analyze"
        self.assertEqual(check_url(url), url)

    def test_file_scheme_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            check_url("file:///etc/passwd")

    def test_plain_http_is_refused_not_silently_upgraded(self):
        # A config asking for http is a mistake somebody should hear about.
        with self.assertRaises(UnsafeURLError):
            check_url("http://app.jobsbyhumans.com/api/analyze")

    def test_ftp_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            check_url("ftp://example.com/track.mp3")

    def test_bare_path_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            check_url("/etc/passwd")

    def test_scheme_without_host_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            check_url("https://")

    def test_refusal_names_the_offending_scheme(self):
        with self.assertRaises(UnsafeURLError) as ctx:
            check_url("file:///tmp/x")
        self.assertIn("file", str(ctx.exception))

    def test_only_https_is_permitted(self):
        self.assertEqual(set(ALLOWED_SCHEMES), {"https"})


class TestRequestObjects(unittest.TestCase):
    def test_a_request_objects_url_is_what_gets_checked(self):
        from catalog_audit import net
        req = urllib.request.Request("file:///etc/passwd")
        with self.assertRaises(UnsafeURLError):
            net.urlopen(req, timeout=1)

    def test_long_urls_are_truncated_in_the_message(self):
        with self.assertRaises(UnsafeURLError) as ctx:
            check_url("file:///" + "a" * 500)
        self.assertLess(len(str(ctx.exception)), 300)


class TestNoDirectSocketCalls(unittest.TestCase):
    """The guard is only worth having if nothing bypasses it."""

    def test_no_module_calls_urlopen_directly(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for py in list((root / "catalog_audit").glob("*.py")) + \
                list((root / "scripts").glob("*.py")):
            if py.name == "net.py":
                continue
            if "urllib.request.urlopen" in py.read_text(encoding="utf-8"):
                offenders.append(py.name)
        self.assertEqual(offenders, [],
                         "these bypass the scheme guard: %s" % offenders)


if __name__ == "__main__":
    unittest.main()


class TestBoundedReads(unittest.TestCase):
    """A Content-Length header is a claim, not a constraint.

    Reading a response whole without a bound lets the far end decide how much
    memory this process allocates.
    """

    class FakeResponse:
        def __init__(self, size):
            self.size = size

        def read(self, n=None):
            return b"x" * (self.size if n is None else min(self.size, n))

    def test_a_small_response_reads_normally(self):
        from catalog_audit import net
        self.assertEqual(len(net.read_capped(self.FakeResponse(1024))), 1024)

    def test_an_oversized_response_is_refused_not_truncated(self):
        # Truncating would surface later as a JSON parse error and send
        # whoever is debugging after a schema problem that does not exist.
        from catalog_audit import net
        with self.assertRaises(net.ResponseTooLargeError):
            net.read_capped(self.FakeResponse(1024), limit=512)

    def test_a_response_exactly_at_the_limit_is_allowed(self):
        from catalog_audit import net
        self.assertEqual(len(net.read_capped(self.FakeResponse(512), limit=512)),
                         512)

    def test_the_default_limit_is_finite_and_sane(self):
        from catalog_audit import net
        self.assertGreater(net.MAX_RESPONSE_BYTES, 1 << 20)
        self.assertLess(net.MAX_RESPONSE_BYTES, 1 << 30)


class TestLinkSafety(unittest.TestCase):
    def test_https_links_are_safe(self):
        from catalog_audit import net
        self.assertTrue(net.is_safe_link("https://example.com/a.png"))

    def test_script_schemes_are_not(self):
        from catalog_audit import net
        for url in ("javascript:alert(1)", "data:text/html,x",
                    "vbscript:x", "file:///etc/passwd", "//evil.com/x"):
            with self.subTest(url=url):
                self.assertFalse(net.is_safe_link(url))

    def test_junk_input_is_not_safe_and_does_not_raise(self):
        from catalog_audit import net
        for value in ("", None, 12345, "   "):
            with self.subTest(value=value):
                self.assertFalse(net.is_safe_link(value))
