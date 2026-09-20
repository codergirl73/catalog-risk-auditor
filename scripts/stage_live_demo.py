#!/usr/bin/env python3
"""Put a never-scored track in front of the live UI, ready for another take.

The live demo is only a live demo once per track: the response is cached by
file hash, so the second click replays instantly and the forty-five seconds
of real network time -- the thing being demonstrated -- does not happen.

This swaps in a track that has never been sent. The running server does not
need restarting: it re-reads the catalog folder on every run, so staging a
new file is enough, and the next click on Run audit is a genuinely fresh
call.

    python3 scripts/stage_live_demo.py            # whatever is next
    python3 scripts/stage_live_demo.py --ai       # a Suno or Udio generation
    python3 scripts/stage_live_demo.py --human    # a real netlabel recording

An AI track makes the better live moment -- a verdict of ai at high
confidence, sometimes with the generator named -- but say which kind you
staged when you narrate it. A demo that only ever shows the dramatic case,
without saying so, is the thing this whole project exists to argue against.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from catalog_audit import config  # noqa: E402

CATALOG = ROOT / "data" / "catalog"
STAGE = ROOT / "data" / "live_demo"

# Large files spend the demo uploading rather than analysing. The API ceiling
# is higher; this is about the length of the pause on camera.
COMFORTABLE_MB = 9.0


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            sha.update(chunk)
    return sha.hexdigest()


def scored_already() -> set:
    """Hashes of every track whose response is already cached."""
    return {p.stem for p in config.CACHE_DIR.glob("*.json")}


def unscored(labels, cached: set) -> list:
    out = []
    for label in labels:
        folder = CATALOG / label
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.mp3")):
            if digest(path) in cached:
                continue
            out.append((label, path))
    return out


def stage(label: str, track: Path) -> Path:
    """Replace the staged catalog with this one track.

    A hard link rather than a symlink: the auditor refuses to follow a link
    that resolves outside the catalog it was found in, which is the right
    behaviour for a folder somebody else assembled and would otherwise make
    the staged track invisible.
    """
    if STAGE.exists():
        shutil.rmtree(STAGE)
    destination = STAGE / label
    destination.mkdir(parents=True)
    staged = destination / track.name
    try:
        staged.hardlink_to(track)
    except OSError:
        shutil.copy2(track, staged)
    return staged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ai", action="store_true",
                       help="stage a Suno or Udio generation")
    group.add_argument("--human", action="store_true",
                       help="stage a real netlabel recording")
    parser.add_argument("--max-mb", type=float, default=COMFORTABLE_MB,
                        help="skip anything larger (default %.0f)"
                             % COMFORTABLE_MB)
    args = parser.parse_args(argv)

    labels = ["ai"] if args.ai else ["human"] if args.human else ["ai", "human"]
    cached = scored_already()
    candidates = [(label, path) for label, path in unscored(labels, cached)
                  if path.stat().st_size <= args.max_mb * 1e6]

    if not candidates:
        print("No unscored track left matching that request.", file=sys.stderr)
        print("Every one has been sent already, so every run would replay "
              "from cache.", file=sys.stderr)
        return 1

    label, track = candidates[0]
    staged = stage(label, track)

    remaining = {}
    for other_label, path in unscored(["ai", "human"], cached):
        if path != track:
            remaining[other_label] = remaining.get(other_label, 0) + 1

    print("staged  %s" % staged.name)
    print("  kind        %s" % ("AI generation (expect a suspect verdict)"
                                if label == "ai"
                                else "human recording (expect clean)"))
    print("  size        %.1f MB" % (track.stat().st_size / 1e6))
    print("  never sent  yes — the next run is a real call, ~48s")
    print()
    print("  left for later takes: %d AI, %d human"
          % (remaining.get("ai", 0), remaining.get("human", 0)))
    print()
    print("The server on :8766 picks this up on its own. Just click Run audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
