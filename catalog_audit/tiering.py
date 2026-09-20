"""Sorting assets into clean, contested and suspect.

The contested band does the real work. A detector that only ever says yes or
no forces a buyer to act on a number the model itself is not sure about; this
one hands those cases to a person and says why.

The thresholds are not ours. HumanStandard publishes three calibrated
operating points with their false-positive rates attached, and this module
tiers on those rather than on numbers we picked. Where the operating points
disagree, the track is borderline by the detector's own account, and that is
exactly the case a person should hear.
"""

from __future__ import annotations

from . import config
from .models import Asset, Tier, TrackScore


def _timestamp(index: int, window_s: float = 2.5) -> str:
    total = int(index * window_s)
    return "%d:%02d" % (total // 60, total % 60)


def _evidence(score: TrackScore) -> str:
    """The supporting detail a reviewer needs, in one sentence.

    An escalation without evidence is just a shrug. This is what turns
    "we could not settle it" into something a person can act on.
    """
    bits = []
    if score.origin:
        if score.origin_summary:
            bits.append("Attributed to %s: %s"
                        % (score.origin.title(), score.origin_summary))
        else:
            bits.append("Attributed to %s (%.0f%% confidence)."
                        % (score.origin.title(), score.origin_confidence * 100))
    if score.risk_timeline:
        idx, peak = score.peak_risk_window
        if peak >= 0.6:
            bits.append("Risk peaks at %.0f%% around %s."
                        % (peak * 100, _timestamp(idx)))
    return " " + " ".join(bits) if bits else ""


def _by_tier_verdicts(score: TrackScore) -> tuple:
    """Tier on HumanStandard's own calibrated operating points.

    The three tiers are published with their false-positive rates, so there is
    no reason to invent thresholds on top of them:

        press_safe  ~0%     FPR -- certification
        human_safe  ~1-2%   FPR -- auto-reject at distribution
        recall      ~5-10%  FPR -- manual-review net

    An acquisition is an auto-reject decision with money attached, so
    human_safe is the gate for calling an asset suspect. Anything the widest
    net still calls human is clean. Everything else is, by HumanStandard's own
    guidance, borderline -- and borderline goes to a person.
    """
    tiers = score.tier_verdicts
    human_safe = tiers.get("human_safe", "")
    recall = tiers.get("recall", "")
    press_safe = tiers.get("press_safe", "")
    evidence = _evidence(score)

    if human_safe == "ai":
        label = ""
        if score.industry_label:
            label = (" Meets the IFPI/RIAA %s definition."
                     % score.industry_label
                     if score.industry_label_status == "meets_definition"
                     else " Suspected %s under the IFPI/RIAA standard."
                     % score.industry_label)
        return (
            Tier.SUSPECT,
            "HumanStandard calls this AI at the human-safe operating point "
            "(~1-2%% false-positive rate).%s%s" % (label, evidence),
        )

    if score.headline_verdict == "ai_generated_suspected":
        return (
            Tier.CONTESTED,
            "Certification declined, but attribution and detection signals "
            "indicate AI-generated primary elements. HumanStandard recommends "
            "stem-level verification.%s" % evidence,
        )

    if recall == "human":
        return (
            Tier.CLEAN,
            "Reads human at every operating point, including the widest "
            "(~5-10% false-positive) recall net.",
        )

    if score.confidence < config.MIN_CONFIDENCE:
        return (
            Tier.CONTESTED,
            "Detector confidence %.2f is below the %.2f floor, so the verdict "
            "is not relied on. Routed for human listening.%s"
            % (score.confidence, config.MIN_CONFIDENCE, evidence),
        )

    return (
        Tier.CONTESTED,
        "The operating points disagree (press-safe %s, human-safe %s, recall "
        "%s). HumanStandard's own guidance is that a split verdict is "
        "borderline and belongs with a person.%s"
        % (press_safe or "n/a", human_safe or "n/a", recall or "n/a", evidence),
    )


def classify(score: TrackScore) -> tuple:
    """Return (tier, reason). Reason is shown to the buyer, so write it plainly."""
    if not score.ok:
        return Tier.ERROR, "Detection failed: %s" % (score.error or "unknown error")

    # Preferred path: the detector's own calibrated tiers.
    if score.tier_verdicts:
        return _by_tier_verdicts(score)

    # Fallback for a response without tier_verdicts -- an older API, a
    # detail=full request that was not honoured, or a different provider.
    if score.confidence < config.MIN_CONFIDENCE:
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
