#!/usr/bin/env python3
"""Download a human-made catalog from Internet Archive netlabel collections.

Netlabel releases are Creative Commons licensed, freely downloadable, and
made by people -- and indie/netlabel back catalogues are genuinely the kind of
asset small rights funds acquire, so this is a more realistic stand-in for an
acquisition target than a streaming playlist would be.

Stdlib only. Run it from your own terminal, which has internet access:

    python3 scripts/fetch_catalog.py --limit 150

Writes audio to data/catalog/human/ and a manifest to data/human_manifest.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from catalog_audit import net  # noqa: E402

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/"
DOWNLOAD_URL = "https://archive.org/download/"

USER_AGENT = "catalog-risk-auditor/0.1 (hackathon project; contact via repo)"
# Anything smaller is an intro sting or a download artefact, not a track.
MIN_TRACK_BYTES = 200_000

AUDIO_FORMATS = ("VBR MP3", "128Kbps MP3", "64Kbps MP3", "MP3")


def _get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with net.urlopen(req, timeout=timeout) as resp:
        return json.loads(
            net.read_capped(resp).decode("utf-8", errors="replace"))


def search_items(query: str, rows: int) -> list:
    params = {
        "q": query,
        "fl[]": "identifier",
        "rows": str(rows),
        "page": "1",
        "output": "json",
        "sort[]": "downloads desc",
    }
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params, doseq=True)
    data = _get_json(url)
    docs = data.get("response", {}).get("docs", [])
    return [d["identifier"] for d in docs if d.get("identifier")]


def item_tracks(identifier: str, per_item: int) -> list:
    """Pick a few MP3s from one release, with whatever metadata is present."""
    try:
        meta = _get_json(METADATA_URL + identifier)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print("  ! metadata failed for %s: %s" % (identifier, exc))
        return []

    item_meta = meta.get("metadata", {}) or {}
    creator = item_meta.get("creator") or item_meta.get("artist") or ""
    if isinstance(creator, list):
        creator = creator[0] if creator else ""
    licence = item_meta.get("licenseurl", "")

    picked = []
    for f in meta.get("files", []) or []:
        if f.get("format") not in AUDIO_FORMATS:
            continue
        name = f.get("name", "")
        if not name.lower().endswith(".mp3"):
            continue
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size < MIN_TRACK_BYTES:    # skip clips and artefacts
            continue
        picked.append({
            "identifier": identifier,
            "name": name,
            "title": f.get("title") or Path(name).stem,
            "artist": f.get("artist") or creator or "Unknown",
            "license": licence,
            "size": size,
        })
        if len(picked) >= per_item:
            break
    return picked


def download(track: dict, dest_dir: Path) -> Path | None:
    raw = Path(track["name"]).name
    stem, _, ext = raw.rpartition(".")
    stem = stem or raw
    # Truncate the stem only. Slicing the whole name can cut the extension
    # off, and a file with no extension is invisible to the auditor's
    # inventory -- it silently drops out of the catalog.
    stem = "%s__%s" % (track["identifier"][:40], stem.replace("/", "_"))
    stem = "".join(ch for ch in stem if ch.isalnum() or ch in "._-")[:110]
    ext = "".join(ch for ch in ext if ch.isalnum())[:5].lower() or "mp3"
    out = dest_dir / ("%s.%s" % (stem, ext))
    if out.exists() and out.stat().st_size > 0:
        return out

    # The identifier comes from a remote search result. Quoting it keeps a
    # value containing ?, # or .. from rewriting the path we meant to ask for.
    url = (DOWNLOAD_URL
           + urllib.parse.quote(track["identifier"], safe="")
           + "/" + urllib.parse.quote(track["name"]))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with net.urlopen(req, timeout=90) as resp, out.open("wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:
        print("  ! download failed %s: %s" % (track["name"][:50], exc))
        if out.exists():
            out.unlink()
        return None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=150,
                    help="total tracks to download (default 150)")
    ap.add_argument("--per-item", type=int, default=4,
                    help="max tracks from any single release (default 4)")
    ap.add_argument("--query", default="collection:netlabels AND format:(MP3)",
                    help="Internet Archive search query")
    ap.add_argument("--out", default=str(ROOT / "data" / "catalog" / "human"))
    args = ap.parse_args(argv)

    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)

    print("Searching Internet Archive...")
    try:
        identifiers = search_items(args.query, rows=max(40, args.limit // 2))
    except Exception as exc:
        print("Search failed: %s" % exc, file=sys.stderr)
        return 1
    print("  %d releases found" % len(identifiers))

    manifest = []
    for ident in identifiers:
        if len(manifest) >= args.limit:
            break
        tracks = item_tracks(ident, args.per_item)
        for t in tracks:
            if len(manifest) >= args.limit:
                break
            path = download(t, dest)
            if path is None:
                continue
            t["filename"] = path.name
            manifest.append(t)
            print("  [%3d/%d] %s" % (len(manifest), args.limit, path.name[:64]))

    if not manifest:
        print("Nothing downloaded. Try a different --query.", file=sys.stderr)
        return 1

    man_path = ROOT / "data" / "human_manifest.csv"
    man_path.parent.mkdir(parents=True, exist_ok=True)
    with man_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "filename", "title", "artist", "identifier", "license"])
        w.writeheader()
        for t in manifest:
            w.writerow({k: t.get(k, "") for k in w.fieldnames})

    print("\n%d tracks in %s" % (len(manifest), dest))
    print("manifest: %s" % man_path)
    print("\nNext: put AI-generated tracks in data/catalog/ai/, then run")
    print("      python3 scripts/build_dataset.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
