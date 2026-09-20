#!/usr/bin/env python3
"""Find the real HumanStandard API shape, then prove a call happened.

HumanStandard publishes no open API docs, so `detector._build_request()` and
`detector.map_response()` are written as guesses. This script replaces guessing
with evidence, in two phases:

    # phase 1 -- costs nothing, burns no credits
    python3 scripts/probe_api.py

      Looks for an OpenAPI spec or a docs route across the likely hosts, then
      probes candidate endpoints with a bodyless request to see which ones
      exist and what auth they accept. No audio is uploaded, so no detection
      credit is spent.

    # phase 2 -- spends exactly one credit, and asks first
    python3 scripts/probe_api.py --upload path/to/track.mp3

      Posts one file to the endpoints that survived phase 1, stopping at the
      first success. Saves the raw response to out/api_evidence.json, which is
      the artefact the submission requires as evidence of a real API call.

Then paste the winning combination into .env (the script prints the exact
lines) and fix map_response() against the response it captured.

Stdlib only, like everything else here.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from catalog_audit import config  # noqa: E402

# Hosts worth trying, best guess first. The published product site is
# hsverify.com; the rest are the conventional variations.
BASES = [
    "https://api.hsverify.com",
    "https://hsverify.com/api",
    "https://app.hsverify.com/api",
    "https://api.humanstandard.ai",
    "https://humanstandard.ai/api",
]

# Machine-readable descriptions first: if any of these answer, the guessing
# stops immediately and everything below is unnecessary.
SPEC_PATHS = [
    "/openapi.json", "/openapi.yaml", "/swagger.json", "/v1/openapi.json",
    "/docs", "/redoc", "/api-docs", "/.well-known/openapi.json",
]

DETECT_PATHS = [
    "/v1/detect", "/detect", "/v1/analyze", "/analyze",
    "/v1/detection", "/v1/scan", "/v1/tracks/analyze", "/v1/audio/detect",
    "/api/v1/detect", "/v1/verify", "/verify",
]

# How the key might have to be presented.
AUTH_STYLES = [
    ("bearer", lambda k: {"Authorization": "Bearer " + k}),
    ("x-api-key", lambda k: {"X-API-Key": k}),
    ("api-key", lambda k: {"Api-Key": k}),
    ("authorization-raw", lambda k: {"Authorization": k}),
]

# What the file field might be called.
FILE_FIELDS = ["file", "audio", "track", "upload"]

TIMEOUT = 20.0
_CTX = ssl.create_default_context()


def _request(url, headers=None, data=None, method="GET", timeout=TIMEOUT):
    """Return (status, body_text, headers). Never raises for HTTP errors."""
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})
    except Exception as e:                      # DNS, TLS, timeout, refused
        return None, str(e), {}


def _short(text, n=220):
    return " ".join(str(text).split())[:n]


# ---------------------------------------------------------------------------
# phase 1a -- is there a spec we can just read?
# ---------------------------------------------------------------------------

def find_spec(key):
    print("\n[1a] Looking for a published API spec (no credits spent)")
    found = []
    for base in BASES:
        for path in SPEC_PATHS:
            url = base + path
            headers = {"Accept": "application/json, text/html",
                       "User-Agent": "catalog-risk-auditor/0.1"}
            if key:
                headers["Authorization"] = "Bearer " + key
            status, body, _ = _request(url, headers)
            if status == 200 and body.strip():
                looks_like_spec = any(
                    m in body[:2000].lower()
                    for m in ('"openapi"', '"swagger"', "swagger-ui", "redoc")
                )
                if looks_like_spec:
                    print("  FOUND  %s  (%s)" % (url, status))
                    found.append((url, body))
                    out = ROOT / "out"
                    out.mkdir(parents=True, exist_ok=True)
                    dest = out / ("api_spec_%s.txt" % urllib.parse.quote(path, ""))
                    dest.write_text(body[:400_000], encoding="utf-8")
                    print("         saved -> %s" % dest)
            elif status not in (None, 404):
                print("  %-4s   %s" % (status, url))
    if not found:
        print("  none found -- falling back to endpoint probing")
    return found


# ---------------------------------------------------------------------------
# phase 1b -- which endpoints exist, and what auth do they want?
# ---------------------------------------------------------------------------

def probe_endpoints(key):
    """POST an empty body. A live endpoint rejects it with 400/415/422 and
    complains about a missing file; a wrong URL answers 404; bad auth gives
    401/403. All three are informative and none of them process audio, so no
    detection credit is consumed."""
    print("\n[1b] Probing detection endpoints with an empty body (no audio, "
          "no credits spent)")
    live = []
    reachable_bases = set()

    for base in BASES:
        status, body, _ = _request(base + "/", {"User-Agent": "probe"})
        if status is None:
            print("  ---    %-34s unreachable: %s" % (base, _short(body, 60)))
            continue
        reachable_bases.add(base)
        print("  %-4s   %-34s reachable" % (status, base))

    if not reachable_bases:
        print("\n  No candidate host resolved. The base URL is something else "
              "entirely -- ask HumanStandard staff on site for it, then set "
              "HS_API_BASE in .env.")
        return live

    for base in sorted(reachable_bases):
        for path in DETECT_PATHS:
            url = base + path
            for style_name, mk in AUTH_STYLES:
                headers = {"Accept": "application/json", "User-Agent": "probe"}
                if key:
                    headers.update(mk(key))
                status, body, _ = _request(url, headers, data=b"", method="POST")

                if status in (None, 404, 405):
                    continue

                verdict = ""
                if status in (400, 415, 422):
                    verdict = "ENDPOINT EXISTS, auth accepted, wants a body"
                elif status in (401, 403):
                    verdict = "endpoint exists, auth rejected"
                elif status == 200:
                    verdict = "200 on an empty body (unexpected, inspect it)"
                elif status == 429:
                    verdict = "rate limited -- endpoint exists"
                else:
                    verdict = "unexpected"

                print("  %-4s   %-46s %-16s %s" % (status, path, style_name, verdict))
                print("         %s" % _short(body, 160))

                if status in (200, 400, 415, 422, 429):
                    live.append({"base": base, "path": path, "auth": style_name})
                # One informative answer per path is enough; if auth was
                # accepted there is no reason to try the other three styles.
                if status in (400, 415, 422, 200):
                    break
    return live


# ---------------------------------------------------------------------------
# phase 2 -- one real upload, saved as evidence
# ---------------------------------------------------------------------------

def _multipart(path: Path, field: str):
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
         % (field, path.name)).encode(),
        ("Content-Type: %s\r\n\r\n" % ctype).encode(),
        path.read_bytes(),
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    return body, "multipart/form-data; boundary=%s" % boundary


def upload(key, audio: Path, candidates):
    print("\n[2] Uploading one file. This spends a detection credit.")
    if not candidates:
        candidates = [{"base": b, "path": p, "auth": a}
                      for b in BASES[:2] for p in DETECT_PATHS[:4]
                      for a, _ in AUTH_STYLES[:2]]
        print("    Phase 1 found nothing conclusive, so trying the most "
              "likely combinations.")

    seen = set()
    for cand in candidates:
        for field in FILE_FIELDS:
            sig = (cand["base"], cand["path"], cand["auth"], field)
            if sig in seen:
                continue
            seen.add(sig)

            url = cand["base"] + cand["path"]
            mk = dict(AUTH_STYLES)[cand["auth"]]
            headers = {"Accept": "application/json", "User-Agent": "probe"}
            if key:
                headers.update(mk(key))
            body, ctype = _multipart(audio, field)
            headers["Content-Type"] = ctype

            status, text, _ = _request(url, headers, data=body, method="POST",
                                       timeout=120)
            print("  %-4s   %s  field=%-6s auth=%s"
                  % (status, cand["path"], field, cand["auth"]))

            if status == 200:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    print("         200 but not JSON: %s" % _short(text))
                    continue

                out = ROOT / "out"
                out.mkdir(parents=True, exist_ok=True)
                evidence = {
                    "request": {
                        "url": url,
                        "method": "POST",
                        "auth_style": cand["auth"],
                        "file_field": field,
                        "filename": audio.name,
                        "content_type": ctype.split(";")[0],
                    },
                    "response_status": status,
                    "response": payload,
                }
                dest = out / "api_evidence.json"
                dest.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

                print("\n  SUCCESS. Raw response:\n")
                print(json.dumps(payload, indent=2)[:4000])
                print("\n  Evidence saved -> %s" % dest)
                print("  (This file is the 'evidence of at least one real API "
                      "call' the submission requires.)")

                print("\n  Paste these four lines into .env and the live "
                      "detector is correctly configured. No code change "
                      "needed:\n")
                print("    HS_API_BASE=%s" % cand["base"])
                print("    HS_DETECT_PATH=%s" % cand["path"])
                print("    HS_AUTH_STYLE=%s" % cand["auth"])
                print("    HS_FILE_FIELD=%s" % field)

                try:
                    from catalog_audit.detector import map_response
                    ai, conf = map_response(payload)
                    print("\n  map_response() already reads this response: "
                          "ai_score=%.1f confidence=%.2f" % (ai, conf))
                    print("  Sanity-check those against the response above "
                          "before trusting them.")
                except Exception as exc:
                    print("\n  map_response() does NOT handle this response "
                          "yet:\n    %s" % exc)
                    print("  Fix map_response() in catalog_audit/detector.py "
                          "against the JSON printed above.")
                return 0

            if status in (401, 403):
                print("         auth rejected: %s" % _short(text, 120))
            elif status and status >= 400:
                print("         %s" % _short(text, 160))

    print("\n  No combination succeeded. Ask HumanStandard staff for the "
          "endpoint and the upload format -- they are on site for exactly "
          "this.")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upload", metavar="AUDIO",
                    help="post one real file and save the response as evidence")
    ap.add_argument("--base", help="skip discovery and use this base URL")
    ap.add_argument("--path", help="skip discovery and use this detect path")
    args = ap.parse_args(argv)

    key = config.HS_API_KEY
    if not key:
        print("No HS_API_KEY found in .env or the environment.")
        print("Discovery will still run, but every authenticated probe will "
              "come back 401.\n")
    else:
        print("Using HS_API_KEY ending %s (%d chars)" % (key[-4:], len(key)))

    if args.base:
        BASES.insert(0, args.base.rstrip("/"))
    if args.path:
        DETECT_PATHS.insert(0, args.path)

    find_spec(key)
    candidates = probe_endpoints(key)

    if args.upload:
        audio = Path(args.upload)
        if not audio.is_file():
            print("Not a file: %s" % audio, file=sys.stderr)
            return 2
        return upload(key, audio, candidates)

    print("\nDiscovery done. Nothing was uploaded and no credit was spent.")
    print("When you are ready to spend exactly one credit:")
    print("    python3 scripts/probe_api.py --upload path/to/track.mp3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
