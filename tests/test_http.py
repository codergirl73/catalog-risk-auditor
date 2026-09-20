"""The one place in the project that opens a socket.

urlopen is a URL opener, not a web client: left unchecked it will read
file:///etc/passwd and hand back the contents as though a server had sent
them. Every outbound request goes through this module so that check happens
once rather than being trusted in four places.
"""

import unittest
import urllib.request

from catalog_audit.http import ALLOWED_SCHEMES, UnsafeURLError, check_url


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
        from catalog_audit import http
        req = urllib.request.Request("file:///etc/passwd")
        with self.assertRaises(UnsafeURLError):
            http.urlopen(req, timeout=1)

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
            if py.name == "http.py":
                continue
            if "urllib.request.urlopen" in py.read_text(encoding="utf-8"):
                offenders.append(py.name)
        self.assertEqual(offenders, [],
                         "these bypass the scheme guard: %s" % offenders)


if __name__ == "__main__":
    unittest.main()
