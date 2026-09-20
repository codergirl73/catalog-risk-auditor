"""Revenue join and escrow maths."""

import unittest

from catalog_audit import config
from catalog_audit.models import Asset, Royalty, Tier
from catalog_audit.valuation import value


def asset(name, tier, usd):
    return Asset(filename=name, tier=tier,
                 royalty=Royalty(filename=name, annual_usd=usd))


class TestValue(unittest.TestCase):
    def setUp(self):
        self.assets = [
            asset("a", Tier.CLEAN, 60_000),
            asset("b", Tier.CLEAN, 20_000),
            asset("c", Tier.CONTESTED, 10_000),
            asset("d", Tier.SUSPECT, 8_000),
            asset("e", Tier.SUSPECT, 2_000),
        ]

    def test_totals_and_counts(self):
        v = value(self.assets, multiple=10.0)
        self.assertEqual(v.track_count, 5)
        self.assertEqual(v.annual_revenue_usd, 100_000)
        self.assertEqual(v.asking_price_usd, 1_000_000)
        self.assertEqual(v.clean_count, 2)
        self.assertEqual(v.contested_count, 1)
        self.assertEqual(v.suspect_count, 2)

    def test_revenue_shares(self):
        v = value(self.assets, multiple=10.0)
        self.assertEqual(v.suspect_revenue_usd, 10_000)
        self.assertAlmostEqual(v.suspect_revenue_share, 0.10, places=4)
        self.assertAlmostEqual(v.contested_revenue_share, 0.10, places=4)

    def test_escrow_weights_contested_at_half(self):
        config_weight = config.CONTESTED_ESCROW_WEIGHT
        v = value(self.assets, multiple=10.0)
        expected = (10_000 + 10_000 * config_weight) * 10.0
        self.assertAlmostEqual(v.recommended_escrow_usd, expected, places=2)

    def test_asking_price_overrides_multiple(self):
        v = value(self.assets, multiple=10.0, asking_price_usd=750_000)
        self.assertEqual(v.asking_price_usd, 750_000)

    def test_count_share_differs_from_revenue_share(self):
        # The point of the tool: 40% of tracks, 10% of revenue.
        v = value(self.assets, multiple=10.0)
        self.assertAlmostEqual(v.suspect_share_by_count, 0.4, places=4)
        self.assertAlmostEqual(v.suspect_revenue_share, 0.1, places=4)

    def test_empty_catalog_does_not_divide_by_zero(self):
        v = value([], multiple=10.0)
        self.assertEqual(v.annual_revenue_usd, 0)
        self.assertEqual(v.suspect_revenue_share, 0.0)
        self.assertEqual(v.suspect_share_by_count, 0.0)

    def test_assets_without_revenue_contribute_nothing(self):
        assets = [*self.assets, Asset(filename="z", tier=Tier.SUSPECT)]
        v = value(assets, multiple=10.0)
        self.assertEqual(v.suspect_revenue_usd, 10_000)
        self.assertEqual(v.suspect_count, 3)


if __name__ == "__main__":
    unittest.main()
