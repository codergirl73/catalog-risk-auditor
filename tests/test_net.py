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


class TestRedirectPinning(unittest.TestCase):
    """Pinning only the first URL pins nothing.

    urllib permits redirects to http, https and ftp, and copies the request
    headers onto the new request -- so an https endpoint can 302 to plain
    http and the bearer token follows it in cleartext.
    """

    def handler(self):
        from catalog_audit import net
        return net._PinnedRedirectHandler()

    def authed_request(self):
        req = urllib.request.Request("https://api.example.com/analyze")
        req.add_header("Authorization", "Bearer SECRET-TOKEN")
        req.add_header("X-API-Key", "SECRET-TOKEN")
        return req

    def redirect_to(self, url):
        return self.handler().redirect_request(
            self.authed_request(), None, 302, "Found", {}, url)

    def test_a_downgrade_to_http_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            self.redirect_to("http://evil.example.com/x")

    def test_a_redirect_to_ftp_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            self.redirect_to("ftp://evil.example.com/x")

    def test_a_redirect_to_a_file_url_is_refused(self):
        with self.assertRaises(UnsafeURLError):
            self.redirect_to("file:///etc/passwd")

    def test_credentials_do_not_follow_to_another_host(self):
        new = self.redirect_to("https://other.example.com/x")
        leaked = [k for k in new.headers
                  if k.lower() in ("authorization", "x-api-key")]
        self.assertEqual(leaked, [], "credentials followed to a new host")

    def test_credentials_survive_a_same_host_redirect(self):
        new = self.redirect_to("https://api.example.com/analyze/v2")
        kept = sorted(k.lower() for k in new.headers
                      if k.lower() in ("authorization", "x-api-key"))
        self.assertEqual(kept, ["authorization", "x-api-key"])

    def test_host_comparison_ignores_case_and_port_absence(self):
        from catalog_audit import net
        self.assertEqual(net._host("https://API.Example.com/x"),
                         net._host("https://api.example.com/y"))

    def test_the_opener_uses_the_pinned_handler(self):
        from catalog_audit import net
        self.assertTrue(any(isinstance(h, net._PinnedRedirectHandler)
                            for h in net._OPENER.handlers))


class TestKeyRedaction(unittest.TestCase):
    """An error body is quoted back to whoever is debugging, and the same
    text reaches the cache and audit.json."""

    def test_the_key_is_removed_from_surfaced_text(self):
        from catalog_audit import config
        original = config.HS_API_KEY
        try:
            config.HS_API_KEY = "hs_live_abcdefghijklmnop"
            out = config.redact("401: sent Bearer hs_live_abcdefghijklmnop")
            self.assertNotIn("hs_live_abcdefghijklmnop", out)
            self.assertIn("<redacted>", out)
        finally:
            config.HS_API_KEY = original

    def test_redaction_is_a_no_op_without_a_key(self):
        from catalog_audit import config
        original = config.HS_API_KEY
        try:
            config.HS_API_KEY = ""
            self.assertEqual(config.redact("nothing to hide"), "nothing to hide")
        finally:
            config.HS_API_KEY = original

    def test_a_short_value_is_not_treated_as_a_key(self):
        # Redacting a two-character "key" would scrub half the message.
        from catalog_audit import config
        original = config.HS_API_KEY
        try:
            config.HS_API_KEY = "ab"
            self.assertEqual(config.redact("a table of absolutes"),
                             "a table of absolutes")
        finally:
            config.HS_API_KEY = original


class TestKeyFingerprint(unittest.TestCase):
    """Confirming which key is loaded should not disclose any of it.

    Printing the last few characters is the usual shortcut and it is a real
    if small disclosure: length plus tail narrows a search, and a credential
    reaching stdout is worth objecting to on principle.
    """

    def fingerprint_for(self, key):
        from catalog_audit import config
        original = config.HS_API_KEY
        try:
            config.HS_API_KEY = key
            return config.key_fingerprint()
        finally:
            config.HS_API_KEY = original

    def test_it_carries_no_part_of_the_key(self):
        key = "hs_live_abcdefghijklmnopqrstuvwxyz"
        fingerprint = self.fingerprint_for(key)
        self.assertNotIn(fingerprint, key)
        for size in (3, 4, 6, 8):
            self.assertNotIn(key[-size:], fingerprint)
            self.assertNotIn(key[:size], fingerprint)

    def test_it_does_not_disclose_the_length(self):
        short = self.fingerprint_for("hs_" + "a" * 8)
        long = self.fingerprint_for("hs_" + "a" * 200)
        self.assertEqual(len(short), len(long))

    def test_the_same_key_always_fingerprints_the_same(self):
        self.assertEqual(self.fingerprint_for("hs_abc123"),
                         self.fingerprint_for("hs_abc123"))

    def test_a_changed_key_is_visible_as_a_changed_fingerprint(self):
        self.assertNotEqual(self.fingerprint_for("hs_abc123"),
                            self.fingerprint_for("hs_abc124"))

    def test_an_absent_key_says_so_rather_than_hashing_nothing(self):
        self.assertEqual(self.fingerprint_for(""), "none")

    def test_it_is_short_enough_to_read_aloud(self):
        self.assertLessEqual(len(self.fingerprint_for("hs_abc123")), 16)
