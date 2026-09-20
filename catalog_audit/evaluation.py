"""Scoring the audit against planted ground truth.

A real acquisition has no answer key. A constructed one does, and that is
exactly why it is worth building the catalog deliberately: you can state the
error rate alongside the finding instead of asking anyone to take the number
on faith.

Convention: SUSPECT counts as calling a track AI. CONTESTED counts as
declining to call it, and is reported separately rather than being scored as
right or wrong -- refusing to answer is not the same as answering wrongly.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Evaluation, Tier

# Below this many planted AI tracks, precision and recall are reported with an
# explicit caveat rather than as if they were measurements.
SMALL_SAMPLE = 10


def load_ground_truth(csv_path) -> dict:
    """Read the answer key.

    Expected columns: filename, true_label. Labels are normalised to
    "ai" or "human"; anything else is ignored.
    """
    path = Path(csv_path)
    out: dict = {}
    if not path.exists():
        return out

    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("filename") or "").strip()
            raw = (row.get("true_label") or row.get("label") or "").strip().lower()
            if not name or not raw:
                continue
            if raw in ("ai", "synthetic", "generated", "1", "true"):
                out[name] = "ai"
            elif raw in ("human", "real", "organic", "0", "false"):
                out[name] = "human"
    return out


def load_origins(csv_path) -> dict:
    """Read the answer key's `true_origin` column, where it has one.

    Knowing a track is AI is one claim. Knowing which model made it is a
    second, harder one, and the API offers an answer to it -- so it can be
    graded too.
    """
    path = Path(csv_path)
    out: dict = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("filename") or "").strip()
            origin = (row.get("true_origin") or "").strip().lower()
            if name and origin:
                out[name] = origin
    return out


def attach(assets: list, truth: dict,
           origins: dict | None = None) -> int:
    matched = 0
    origins = origins or {}
    for asset in assets:
        label = truth.get(asset.filename)
        if label:
            asset.truth = label
            matched += 1
        asset.true_origin = origins.get(asset.filename, "")
    return matched


def evaluate(assets: list) -> Evaluation:
    """Compare tier decisions against the planted labels."""
    ev = Evaluation()

    for asset in assets:
        if asset.truth not in ("ai", "human"):
            continue
        ev.labelled += 1

        if asset.tier == Tier.CONTESTED:
            if asset.truth == "ai":
                ev.contested_ai += 1
            else:
                ev.contested_human += 1
            continue

        if asset.tier == Tier.ERROR:
            continue

        called_ai = asset.tier == Tier.SUSPECT

        if called_ai and asset.truth == "ai":
            _score_attribution(ev, asset)
            ev.true_positive += 1
        elif called_ai and asset.truth == "human":
            ev.false_positive += 1
            ev.false_positive_files.append(asset.filename)
        elif not called_ai and asset.truth == "human":
            ev.true_negative += 1
        else:
            ev.false_negative += 1
            ev.false_negative_files.append(asset.filename)

    return ev


def _score_attribution(ev: Evaluation, asset) -> None:
    """Grade the generator attribution on a correctly-caught AI track."""
    if not asset.true_origin:
        return
    ev.origin_labelled += 1
    claimed = (asset.score.origin or "").strip().lower() if asset.score else ""
    # "uncertain" and "human" are both the API declining to name a generator,
    # which is a different thing from naming the wrong one. Counting a
    # declined attribution as an error would punish the detector for the one
    # behaviour this whole tool exists to reward.
    if claimed in ("", "human", "uncertain", "unknown", "none"):
        ev.origin_absent += 1
    elif claimed == asset.true_origin:
        ev.origin_correct += 1
    else:
        ev.origin_wrong += 1
        ev.origin_confusions.append(
            "%s: said %s, was %s" % (asset.filename, claimed,
                                     asset.true_origin))


def attribution_summary(ev: Evaluation) -> str:
    """One line on how well the generator attribution did."""
    if not ev.origin_labelled:
        return ""
    parts = ["Of %d correctly flagged AI track%s whose true generator is "
             "known, %d %s attributed to the right one"
             % (ev.origin_labelled, "" if ev.origin_labelled == 1 else "s",
                ev.origin_correct,
                "was" if ev.origin_correct == 1 else "were")]
    if ev.origin_wrong:
        parts.append("%d to the wrong one (%s)"
                     % (ev.origin_wrong, "; ".join(ev.origin_confusions[:3])))
    if ev.origin_absent:
        parts.append("%d carried no attribution" % ev.origin_absent)
    return ", ".join(parts) + "."


def summary(ev: Evaluation) -> str:
    """One paragraph, written to be read aloud in a demo."""
    if not ev.labelled:
        return ("No ground-truth labels supplied, so no accuracy can be "
                "reported for this run.")

    planted = ev.true_positive + ev.false_negative + ev.contested_ai
    parts = [
        "Against %d labelled tracks: %d of %d planted AI track%s caught, "
        "%d missed, and %d landed in the contested band."
        % (ev.labelled, ev.true_positive, planted,
           " was" if planted == 1 else "s were", ev.false_negative,
           ev.contested_ai)
    ]
    if ev.false_positive:
        parts.append(
            "%d human track%s w%s wrongly flagged as suspect: %s."
            % (ev.false_positive, "" if ev.false_positive == 1 else "s",
               "as" if ev.false_positive == 1 else "ere",
               ", ".join(ev.false_positive_files[:5]))
        )
    else:
        parts.append("No human track was wrongly flagged as suspect.")

    if ev.precision is not None:
        parts.append("Precision %.2f." % ev.precision)
    if ev.recall is not None:
        parts.append("Recall %.2f." % ev.recall)

    # A rate computed over a handful of tracks is a count wearing a decimal
    # point. Say which one it is rather than letting the reader assume.
    attribution = attribution_summary(ev)
    if attribution:
        parts.append(attribution)

    if 0 < planted < SMALL_SAMPLE:
        parts.append(
            "Only %d AI track%s planted, so these are counts rather than "
            "measured rates \u2014 the confidence interval on a sample this "
            "size is wide enough that the figures should be read as "
            "indicative." % (planted, " was" if planted == 1 else "s were"))

    return " ".join(parts)
