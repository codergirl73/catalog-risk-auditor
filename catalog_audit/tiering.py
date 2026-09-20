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


def _timestamp(seconds: float) -> str:
    total = int(max(0.0, seconds))
    return "%d:%02d" % (total // 60, total % 60)


def _evidence(score: TrackScore) -> str:
    """The supporting detail a reviewer needs, in one sentence.

    An escalation without evidence is just a shrug. This is what turns
    "we could not settle it" into something a person can act on.
    """
    bits = []
    if score.industry_label_basis:
        bits.append(score.industry_label_basis[0].rstrip(".") + ".")
    # origin names a reference population, which may be a generator or the
    # verified-human set. Only the former is worth putting in an escalation.
    if score.origin and score.origin.lower() != "human":
        if score.origin_summary:
            bits.append("Attributed to %s: %s"
                        % (score.origin.title(), score.origin_summary))
        else:
            bits.append("Attributed to %s (%.0f%% confidence)."
                        % (score.origin.title(), score.origin_confidence * 100))
    at, peak = score.peak_risk
    if at >= 0 and peak >= 0.6:
        bits.append("Risk peaks at %.0f%% from %s."
                    % (peak * 100, _timestamp(at)))
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
            "AI at the human-safe operating point (~1-2%% false-positive "
            "rate).%s%s" % (label, evidence),
        )

    if score.headline_verdict == "ai_generated_suspected":
        return (
            Tier.CONTESTED,
            "Suspected AI-generated primary elements; certification declined. "
            "Stem-level verification recommended.%s" % evidence,
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
        "Split verdict \u2014 press-safe %s, human-safe %s, recall %s. "
        "Borderline by the detector's own account.%s"
        % (press_safe or "n/a", human_safe or "n/a", recall or "n/a", evidence),
    )


def _by_verdict(score: TrackScore) -> tuple:
    """Tier on the detector's own verdict when tier_verdicts is absent.

    Observed against the live API: responses carry a verdict but not always
    the calibrated tier block, so this is the path that usually runs. The
    verdict is still the detector's own call, which beats re-deriving one from
    a score and a threshold we chose.

    `suspicious` is a real value the published docs do not list. It means the
    screening threshold was cleared and the certification threshold was not --
    which is the contested band stated in their vocabulary rather than ours.
    """
    verdict = score.verdict
    evidence = _evidence(score)

    if verdict == "ai" and score.industry_label_status == "suspected":
        return (
            Tier.CONTESTED,
            "Verdict AI, but certification declined \u2014 suspected rather "
            "than established. Stem-level verification recommended before "
            "this is priced as unownable.%s" % evidence,
        )

    if verdict == "ai":
        label = (" Meets the IFPI/RIAA %s definition." % score.industry_label
                 if score.industry_label else "")
        return (
            Tier.SUSPECT,
            "HumanStandard returns a verdict of AI at %.0f%% confidence.%s%s"
            % (score.confidence * 100, label, evidence),
        )

    if verdict in ("suspicious", "uncertain"):
        return (
            Tier.CONTESTED,
            "HumanStandard declines to call this one: verdict '%s' at %.0f%% "
            "confidence. Screening cleared, certification did not.%s"
            % (verdict, score.confidence * 100, evidence),
        )

    if verdict == "human":
        if score.confidence < config.MIN_CONFIDENCE:
            return (
                Tier.CONTESTED,
                "Verdict human, but only at %.0f%% confidence, below the %.0f%% "
                "floor. Not counted as clean without a listen.%s"
                % (score.confidence * 100, config.MIN_CONFIDENCE * 100,
                   evidence),
            )
        return (
            Tier.CLEAN,
            "HumanStandard returns a verdict of human at %.0f%% confidence."
            % (score.confidence * 100),
        )

    return (
        Tier.CONTESTED,
        "Unrecognised verdict '%s'. Not assumed clean.%s" % (verdict, evidence),
    )


def classify(score: TrackScore) -> tuple:
    """Return (tier, reason). Reason is shown to the buyer, so write it plainly.

    Three paths, most authoritative first: HumanStandard's calibrated
    operating points, then its own verdict, then a score band. Each one is
    the detector's judgement; only the last is ours.
    """
    if not score.ok:
        return Tier.ERROR, "Detection failed: %s" % (score.error or "unknown error")

    # Best: the calibrated operating points, when detail=full returns them.
    if score.tier_verdicts:
        return _by_tier_verdicts(score)

    # Next: the detector's own verdict. This is the path that usually runs.
    if score.verdict:
        return _by_verdict(score)

    # Last: score bands, for a response carrying neither.
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
