"""Turning detection results into the number a buyer negotiates with.

A track count is interesting. A dollar figure is actionable. The join between
the two is the whole point of this tool: synthetic uploads accumulate far
faster than they earn, so share-by-count and share-by-revenue are very
different numbers and only one of them affects the price.
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import config
from .models import Asset, Royalty, Tier, Valuation


def load_royalties(csv_path) -> dict:
    """Read the seller's reported earnings.

    Expected columns: filename, title, artist, annual_usd. Extra columns are
    ignored; a missing file is not fatal, it just means no revenue weighting.
    """
    path = Path(csv_path)
    out: dict = {}
    if not path.exists():
        return out

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("filename") or "").strip()
            if not name:
                continue
            try:
                amount = float(row.get("annual_usd") or 0.0)
            except ValueError:
                amount = 0.0
            out[name] = Royalty(
                filename=name,
                title=(row.get("title") or "").strip(),
                artist=(row.get("artist") or "").strip(),
                annual_usd=amount,
            )
    return out


def attach_royalties(assets: list, royalties: dict) -> int:
    """Join revenue onto assets. Returns how many matched."""
    matched = 0
    for asset in assets:
        r = royalties.get(asset.filename)
        if r:
            asset.royalty = r
            matched += 1
    return matched


def value(assets: list, multiple: float = None,
          asking_price_usd: float = None) -> Valuation:
    """Compute exposure and a recommended escrow.

    Suspect revenue is held back in full: those assets may carry no
    enforceable copyright and their income can be switched off by platform
    policy. Contested revenue is weighted, because uncertainty is not the same
    as a finding.
    """
    multiple = config.DEFAULT_MULTIPLE if multiple is None else multiple

    annual = sum(a.annual_usd for a in assets)
    price = asking_price_usd if asking_price_usd else annual * multiple

    suspect_rev = sum(a.annual_usd for a in assets if a.tier == Tier.SUSPECT)
    contested_rev = sum(a.annual_usd for a in assets if a.tier == Tier.CONTESTED)

    val = Valuation(
        track_count=len(assets),
        annual_revenue_usd=round(annual, 2),
        multiple=multiple,
        asking_price_usd=round(price, 2),
        clean_count=sum(1 for a in assets if a.tier == Tier.CLEAN),
        contested_count=sum(1 for a in assets if a.tier == Tier.CONTESTED),
        suspect_count=sum(1 for a in assets if a.tier == Tier.SUSPECT),
        error_count=sum(1 for a in assets if a.tier == Tier.ERROR),
        suspect_revenue_usd=round(suspect_rev, 2),
        contested_revenue_usd=round(contested_rev, 2),
        suspect_revenue_share=round(suspect_rev / annual, 4) if annual else 0.0,
        contested_revenue_share=round(contested_rev / annual, 4) if annual else 0.0,
    )

    weighted = suspect_rev + contested_rev * config.CONTESTED_ESCROW_WEIGHT
    val.recommended_escrow_usd = round(weighted * multiple, 2)
    val.escrow_basis = (
        "Suspect revenue of $%s held in full plus %.0f%% of contested revenue "
        "of $%s, capitalised at the %.1fx acquisition multiple."
        % (f"{suspect_rev:,.0f}", config.CONTESTED_ESCROW_WEIGHT * 100,
           f"{contested_rev:,.0f}", multiple)
    )
    return val


def headline(val: Valuation) -> str:
    """The sentence that goes on screen."""
    return (
        "%d of %d tracks (%.1f%%) are suspect, carrying $%s of $%s annual "
        "revenue (%.1f%%). Recommended escrow $%s against an asking price of $%s."
        % (val.suspect_count, val.track_count, val.suspect_share_by_count * 100,
           f"{val.suspect_revenue_usd:,.0f}", f"{val.annual_revenue_usd:,.0f}",
           val.suspect_revenue_share * 100,
           f"{val.recommended_escrow_usd:,.0f}",
           f"{val.asking_price_usd:,.0f}")
    )
