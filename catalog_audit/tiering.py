"""Sorting assets into clean, contested and suspect.

The contested band does the real work. A detector that only ever says yes or
no forces a buyer to act on a number the model itself is not sure about; this
one hands those cases to a person and says why.
"""

from __future__ import annotations

from . import config
from .models import Asset, Tier, TrackScore


def classify(score: TrackScore) -> tuple:
    """Return (tier, reason). Reason is shown to the buyer, so write it plainly."""
    if not score.ok:
        return Tier.ERROR, "Detection failed: %s" % (score.error or "unknown error")

    low_confidence = score.confidence < config.MIN_CONFIDENCE

    if low_confidence:
        return (
            Tier.CONTESTED,
            "Detector confidence %.2f is below the %.2f floor, so the %.0f score "
            "is not relied on. Routed for human listening."
            % (score.confidence, config.MIN_CONFIDENCE, score.ai_score),
        )

    if score.ai_score < config.CLEAN_CEILING:
        return (
            Tier.CLEAN,
            "Scores %.0f, below the %.0f clean ceiling."
            % (score.ai_score, config.CLEAN_CEILING),
        )

    if score.ai_score > config.SUSPECT_FLOOR:
        return (
            Tier.SUSPECT,
            "Scores %.0f, above the %.0f suspect floor."
            % (score.ai_score, config.SUSPECT_FLOOR),
        )

    return (
        Tier.CONTESTED,
        "Scores %.0f, inside the %.0f-%.0f contested band. Audio alone does not "
        "settle this one."
        % (score.ai_score, config.CLEAN_CEILING, config.SUSPECT_FLOOR),
    )


def apply(assets: list) -> list:
    """Classify every asset in place and return the ones needing human review."""
    review: list = []
    for asset in assets:
        if asset.score is None:
            asset.tier = Tier.ERROR
            asset.notes.append("No detection result.")
            continue
        tier, reason = classify(asset.score)
        asset.tier = tier
        asset.notes.append(reason)
        if tier in (Tier.CONTESTED, Tier.ERROR):
            review.append(asset)
    return review


def counts(assets: list) -> dict:
    out = {t: 0 for t in Tier}
    for a in assets:
        out[a.tier] = out.get(a.tier, 0) + 1
    return out
