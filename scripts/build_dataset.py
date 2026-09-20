#!/usr/bin/env python3
"""Build the ground-truth and royalty sheets for a constructed catalog.

Folder convention -- this is the whole labelling scheme:

    data/catalog/human/   tracks known to be human-made (CC, from Internet Archive)
    data/catalog/ai/      tracks you generated yourself

Anything under human/ is labelled human, anything under ai/ is labelled ai.

The royalty figures are synthetic and deliberately shaped:

  * Earnings follow a steep power law. Real catalogs are top-heavy -- a
    handful of tracks carry most of the income -- and a flat distribution
    would make the revenue join look trivial.

  * AI tracks earn far less per track than human ones by default. That is not
    a thumb on the scale, it is the observed pattern: Deezer reported AI
    material reaching over half of daily uploads while accounting for only
    1-3% of actual streams. Tune it with --ai-earnings-ratio, or set it to 1.0
    to remove the assumption entirely.

Every number here is generated. Say so in your submission.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Below this many planted tracks the accuracy figures are counts, not rates.
THIN_AI_SET = 5

AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aif", ".aiff", ".ogg"}


def load_manifest(path: Path) -> dict:
    """Titles and artists from the download manifest, if it exists."""
    out: dict = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("filename") or "").strip()
            if name:
                out[name] = row
    return out


# SONICS names its members fake_<id>_<generator>_<n>.mp3, so the generator
# that actually made each track is recoverable from the filename. That turns
# the answer key from "AI or not" into "AI, and by which model", which is a
# second thing the audit can be graded on.
KNOWN_GENERATORS = ("suno", "udio", "musicgen", "audioldm", "musicldm",
                    "stableaudio", "mustango", "elevenlabs", "lyria", "treblo")


def true_origin(filename: str) -> str:
    stem = filename.lower()
    for gen in KNOWN_GENERATORS:
        if gen in stem:
            return gen
    return ""


def collect(catalog_dir: Path) -> list:
    """Find audio under human/ and ai/ and label it by folder."""
    rows = []
    for label in ("human", "ai"):
        sub = catalog_dir / label
        if not sub.is_dir():
            continue
        for p in sorted(sub.rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXT:
                rows.append({
                    "filename": p.name,
                    "true_label": label,
                    "true_origin": true_origin(p.name) if label == "ai" else "",
                })
    return rows


def assign_earnings(rows: list, total_usd: float, ai_ratio: float,
                    seed: int) -> None:
    """Distribute revenue with a steep head and a long tail."""
    # Deliberately seeded and deliberately not cryptographic: these are
    # demonstration royalty figures, and the whole point is that the same seed
    # reproduces the same catalog for anyone re-running the audit.
    rng = random.Random(seed)  # noqa: S311  # nosec B311

    weights = []
    for row in rows:
        # Pareto-ish: most tracks near zero, a few very large.
        w = rng.paretovariate(1.16)
        if row["true_label"] == "ai":
            w *= ai_ratio
        weights.append(w)

    total_weight = sum(weights) or 1.0
    for row, w in zip(rows, weights, strict=True):
        row["annual_usd"] = round(total_usd * w / total_weight, 2)


def write_ground_truth(rows: list, path: Path) -> None:
    """The answer key: what each track really is, and what really made it."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["filename", "true_label", "true_origin"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"filename": row["filename"],
                             "true_label": row["true_label"],
                             "true_origin": row.get("true_origin", "")})


def write_royalties(rows: list, manifest: dict, path: Path) -> None:
    """The seller's reported earnings, as a sheet the auditor can join on."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["filename", "title", "artist", "annual_usd"])
        writer.writeheader()
        for row in rows:
            meta = manifest.get(row["filename"], {})
            default_artist = ("Unknown" if row["true_label"] == "human"
                              else "Generated")
            writer.writerow({
                "filename": row["filename"],
                "title": meta.get("title") or Path(row["filename"]).stem[:60],
                "artist": meta.get("artist") or default_artist,
                "annual_usd": row["annual_usd"],
            })


def warn_about_the_ai_set(rows: list, ai: int, catalog: Path) -> None:
    """Say plainly when the planted set is too thin to measure anything."""
    if not ai:
        print()
        print("  ! No AI tracks found in %s/ai" % catalog)
        print("  ! The audit will run, but with nothing planted there is no")
        print("  ! precision or recall to report and no suspect tier to show.")
        print("  ! Generate a few tracks (Suno, Udio, anything), drop the")
        print("  ! files in %s/ai and run this again." % catalog)
    elif ai < THIN_AI_SET:
        print()
        print("  ! Only %d AI track%s planted. Accuracy figures on a sample"
              % (ai, "" if ai == 1 else "s"))
        print("  ! that small are indicative, not measurements -- the memo")
        print("  ! reports the counts, so say so rather than quoting a rate.")


def describe(rows: list, catalog: Path) -> int:
    """Print what was built, and return the count of planted AI tracks."""
    ai_rows = [r for r in rows if r["true_label"] == "ai"]
    ai = len(ai_rows)
    ai_revenue = sum(r["annual_usd"] for r in ai_rows)
    total = sum(r["annual_usd"] for r in rows)

    generators: dict = {}
    for row in rows:
        origin = row.get("true_origin")
        if origin:
            generators[origin] = generators.get(origin, 0) + 1

    print("catalog: %d tracks (%d human, %d ai)"
          % (len(rows), len(rows) - ai, ai))
    if generators:
        print("planted generators: %s"
              % ", ".join("%s x%d" % (name, n)
                          for name, n in sorted(generators.items())))
    print("planted AI share: %.1f%% by count, %.1f%% by revenue"
          % (100.0 * ai / len(rows),
             100.0 * ai_revenue / total if total else 0.0))

    warn_about_the_ai_set(rows, ai, catalog)
    return ai


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", default=str(ROOT / "data" / "catalog"))
    parser.add_argument("--total-revenue", type=float, default=160_000.0,
                        help="total annual revenue to distribute "
                             "(default 160000)")
    parser.add_argument("--ai-earnings-ratio", type=float, default=0.18,
                        help="how much an AI track earns relative to a human "
                             "one (default 0.18; set 1.0 for no assumption)")
    parser.add_argument("--seed", type=int, default=20260920)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    catalog = Path(args.catalog)
    rows = collect(catalog)
    if not rows:
        print("No audio found under %s/human or %s/ai" % (catalog, catalog))
        print("Run scripts/fetch_catalog.py first, then add your AI tracks.")
        return 1

    manifest = load_manifest(ROOT / "data" / "human_manifest.csv")
    assign_earnings(rows, args.total_revenue, args.ai_earnings_ratio,
                    args.seed)

    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    truth_path = data_dir / "ground_truth.csv"
    royalties_path = data_dir / "royalties.csv"

    write_ground_truth(rows, truth_path)
    write_royalties(rows, manifest, royalties_path)
    describe(rows, catalog)

    print("ground truth: %s" % truth_path)
    print("royalties:    %s" % royalties_path)
    print("\nNext:")
    print("  python3 run.py data/catalog --royalties data/royalties.csv \\")
    print("      --truth data/ground_truth.csv --json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
