"""The memo is a document a buyer opens in a browser.

Its contents include strings that arrived from a third-party API, so the
things that matter are: nothing executes, nothing loads from the network, and
no value from a response can become a live link to somewhere dangerous.
"""

import unittest

from catalog_audit import memo
from catalog_audit.models import (
    Asset,
    AuditResult,
    Royalty,
    Tier,
    TrackScore,
    Valuation,
)

DANGEROUS = [
    "javascript:alert(document.domain)",
    "JaVaScRiPt:alert(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "http://insecure.example.com/map.png",
    "//evil.example.com/map.png",
    "  javascript:alert(1)",
]

SAFE = "https://firebasestorage.googleapis.com/v0/b/x/o/map.png?token=abc"


def result_with(evidence="", reason="why", filename="t.mp3"):
    score = TrackScore(filename, filename, 90.0, 0.9, "humanstandard",
                       verdict="ai", origin_map_evidence=evidence)
    asset = Asset(filename=filename, score=score, tier=Tier.CONTESTED,
                  royalty=Royalty(filename, "T", "A", 10.0), notes=[reason])
    result = AuditResult("Catalog", "/c", assets=[asset], provider="hs")
    result.valuation = Valuation(track_count=1, annual_revenue_usd=10.0,
                                 multiple=15.0, asking_price_usd=150.0,
                                 contested_count=1)
    result.review_queue = [{"filename": filename, "ai_score": 90.0,
                            "annual_usd": 10.0, "reason": reason}]
    return result


class TestDangerousLinks(unittest.TestCase):
    """html.escape stops attribute breakout. It does nothing about schemes."""

    def test_dangerous_schemes_never_become_links(self):
        for url in DANGEROUS:
            with self.subTest(url=url):
                html = memo.render(result_with(evidence=url))
                self.assertNotIn('href="%s"' % url, html)
                self.assertNotIn("javascript:", html.lower())
                self.assertNotIn("vbscript:", html.lower())

    def test_a_dangerous_url_does_not_break_the_document(self):
        html = memo.render(result_with(evidence="javascript:alert(1)"))
        self.assertIn("Human review queue", html)
        self.assertIn("why", html)

    def test_an_https_url_is_linked(self):
        html = memo.render(result_with(evidence=SAFE))
        self.assertIn("similarity map", html)
        self.assertIn("firebasestorage", html)

    def test_external_links_leak_no_referrer_or_opener(self):
        html = memo.render(result_with(evidence=SAFE))
        self.assertIn('rel="noopener noreferrer nofollow"', html)


class TestNothingExecutes(unittest.TestCase):
    def test_no_script_tag_anywhere(self):
        html = memo.render(result_with(evidence=SAFE, reason="<script>x</script>"))
        self.assertNotIn("<script", html.lower())

    def test_a_content_security_policy_forbids_execution(self):
        html = memo.render(result_with())
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("form-action 'none'", html)

    def test_no_inline_event_handler_survives(self):
        hostile = '" onmouseover="alert(1)'
        html = memo.render(result_with(reason=hostile, filename=hostile))
        self.assertNotIn('onmouseover="alert(1)"', html)

    def test_hostile_filenames_are_escaped(self):
        html = memo.render(result_with(filename="<img src=x onerror=alert(1)>"))
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)

    def test_the_document_loads_nothing_over_the_network(self):
        # A memo that needs a network to render is useless on a projector,
        # and a memo that fetches on open is a tracking pixel.
        html = memo.render(result_with())
        for tag in ("<script", "<iframe", "<object", "<embed", "<link "):
            self.assertNotIn(tag, html.lower())


if __name__ == "__main__":
    unittest.main()
