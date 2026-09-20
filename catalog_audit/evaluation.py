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

from .models import Asset, Evaluation, Tier


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


def attach(assets: list, truth: dict) -> int:
    matched = 0
    for asset in assets:
        label = truth.get(asset.filename)
        if label:
            asset.truth = label
            matched += 1
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


def summary(ev: Evaluation) -> str:
    """One paragraph, written to be read aloud in a demo."""
    if not ev.labelled:
        return ("No ground-truth labels supplied, so no accuracy can be "
                "reported for this run.")

    planted = ev.true_positive + ev.false_negative + ev.contested_ai
    parts = [
        "Against %d labelled tracks: %d of %d planted AI tracks were caught, "
        "%d were missed, and %d landed in the contested band."
        % (ev.labelled, ev.true_positive, planted, ev.false_negative,
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

    return " ".join(parts)
