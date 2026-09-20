#!/usr/bin/env python3
"""Verify the HumanStandard integration, then capture evidence of a real call.

Three phases, cheapest first. You can stop after any of them.

    python3 scripts/probe_api.py
        Phase 1 and 2. Checks the key works, then runs a full analyse-and-poll
        cycle against HumanStandard's own ?mock= fixtures -- real endpoint,
        real auth, real response shapes, real polling, and nothing billed.
        This proves the integration end to end for zero credits.

    python3 scripts/probe_api.py --upload data/catalog/human/some.mp3
        Phase 3. One real analysis of one real file. Saves the raw response to
        out/api_evidence.json, which is the artefact the submission requires
        as evidence of a real API call. Costs exactly one credit.

If phase 2 passes and phase 3 fails, the problem is the audio or the account,
not the integration. That distinction is worth five minutes when you have
six hours.

Stdlib only, like everything else here.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from catalog_audit import config                                    # noqa: E402
from catalog_audit.detector import (LiveDetector, map_response,     # noqa: E402
                                    parse_result)

# The four fixtures HumanStandard publishes. "suspicious" is the one this tool
# cares most about: it is the mid-band case that should land in review rather
# than on either side of it.
SCENARIOS = ["human", "ai", "suspicious"]

OK, BAD, WARN = "  [ok] ", "  [!!] ", "  [--] "


def _short(text, n=200):
    return " ".join(str(text).split())[:n]


# ---------------------------------------------------------------------------
# phase 1 -- does the key work at all?
# ---------------------------------------------------------------------------

def check_key() -> bool:
    print("\n[1] Checking the key and the endpoint (no credits)")
    print("    %s%s" % (config.HS_API_BASE, config.HS_DETECT_PATH))

    if not config.HS_API_KEY:
        print(BAD + "No HS_API_KEY set. Copy .env.example to .env and put the "
                    "key in it.")
        return False
    print(OK + "key present, ending %s (%d chars)"
          % (config.HS_API_KEY[-4:], len(config.HS_API_KEY)))

    det = LiveDetector.__new__(LiveDetector)
    req = urllib.request.Request(
        config.HS_API_BASE + config.HS_DETECT_PATH + det._query({"mock": "ai"}),
        data=b"", method="POST")
    for k, v in det.base_headers().items():
        req.add_header(k, v)

    try:
        with http.urlopen(req, timeout=30) as resp:
            code, body = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        code = exc.code
        body = exc.read().decode("utf-8", "replace")
    except Exception as exc:
        print(BAD + "Cannot reach the API: %s" % exc)
        print("       Check HS_API_BASE in .env.")
        return False

    if code == 403 and "1010" in body:
        print(BAD + "403 error code 1010 -- Cloudflare rejected the client "
                    "signature, not the key.")
        print("       Set HS_USER_AGENT in .env to any real identifier.")
        return False
    if code in (401, 403):
        print(BAD + "%s -- the key was rejected: %s" % (code, _short(body)))
        print("       Check the key, and that HS_AUTH_STYLE is 'bearer'.")
        return False
    if code == 404:
        print(BAD + "404 -- wrong path. Check HS_DETECT_PATH in .env.")
        return False

    print(OK + "endpoint answered %s, auth accepted" % code)
    return True


# ---------------------------------------------------------------------------
# phase 2 -- does the whole cycle work, for free?
# ---------------------------------------------------------------------------

def check_with_fixtures() -> bool:
    """Run submit -> poll -> map -> tier against HumanStandard's fixtures.

    Mock mode returns real-shaped responses and bills nothing, so this
    exercises every line of the integration except the model itself.
    """
    print("\n[2] Full cycle against HumanStandard's ?mock= fixtures "
          "(no credits)")

    from catalog_audit.tiering import classify
    from catalog_audit.models import TrackScore

    original = config.HS_MOCK_SCENARIO
    all_ok = True
    try:
        for scenario in SCENARIOS:
            config.HS_MOCK_SCENARIO = scenario
            det = LiveDetector()
            det.budget = None
            try:
                payload = det._call(Path(__file__))   # mock ignores the body
            except Exception as exc:
                print(BAD + "%-11s %s" % (scenario, _short(exc, 150)))
                all_ok = False
                continue

            try:
                ai, conf = map_response(payload)
            except ValueError as exc:
                print(BAD + "%-11s response did not map: %s"
                      % (scenario, _short(exc, 140)))
                print("       Fix map_response() in catalog_audit/detector.py "
                      "against:\n       " + _short(json.dumps(payload), 300))
                all_ok = False
                continue

            fields = parse_result(payload)
            score = TrackScore(filename="fixture", path="fixture", ai_score=ai,
                               confidence=conf, provider="humanstandard",
                               **fields)
            tier, _ = classify(score)

            flag = "" if fields["mock"] else "  <- NOT flagged as mock!"
            print(OK + "%-11s verdict=%-9s score=%5.1f conf=%.2f -> %-9s%s"
                  % (scenario, fields["verdict"] or "?", ai, conf,
                     tier.value, flag))
            if not fields["mock"]:
                all_ok = False
    finally:
        config.HS_MOCK_SCENARIO = original

    if all_ok:
        print(OK + "submit, poll, map and tier all work. Nothing was billed.")
    return all_ok


# ---------------------------------------------------------------------------
# phase 3 -- one real call, saved as evidence
# ---------------------------------------------------------------------------

def real_call(audio: Path) -> int:
    print("\n[3] One real analysis of %s" % audio.name)
    print("    This spends one credit. Cold starts take 20-30s.")

    det = LiveDetector()
    det.budget = None
    try:
        payload = det._call(audio)
    except Exception as exc:
        print(BAD + "Failed: %s" % exc)
        return 1

    if payload.get("mock") is True:
        print(BAD + "That came back as a mock fixture. Unset HS_MOCK_SCENARIO "
                    "in .env for a real analysis.")
        return 1

    try:
        ai, conf = map_response(payload)
    except ValueError as exc:
        print(BAD + "Real response did not map: %s" % exc)
        print(json.dumps(payload, indent=2)[:3000])
        return 1

    fields = parse_result(payload)
    out = ROOT / "out"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "api_evidence.json"
    dest.write_text(json.dumps({
        "request": {
            "url": config.HS_API_BASE + config.HS_DETECT_PATH + det._query(),
            "method": "POST",
            "auth": "Authorization: Bearer <redacted>",
            "file": audio.name,
        },
        "response": payload,
        "interpreted": {"ai_score": ai, "confidence": conf, **fields},
    }, indent=2), encoding="utf-8")

    print("\n  Raw response:\n")
    print(json.dumps(payload, indent=2)[:3500])
    print("\n" + OK + "verdict=%s confidence=%.2f -> ai_score %.1f"
          % (fields["verdict"] or "?", conf, ai))
    if fields["origin"]:
        print(OK + "attributed to %s: %s"
              % (fields["origin"], fields["origin_summary"] or "no summary"))
    if fields["tier_verdicts"]:
        print(OK + "tier verdicts: %s" % fields["tier_verdicts"])
    print(OK + "evidence saved -> %s" % dest)
    print("       That file is the 'evidence of at least one real API call' "
          "the submission asks for.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upload", metavar="AUDIO",
                    help="spend one credit on a real analysis and save it")
    ap.add_argument("--skip-fixtures", action="store_true",
                    help="skip phase 2")
    args = ap.parse_args(argv)

    if not check_key():
        return 1

    if not args.skip_fixtures and not check_with_fixtures():
        print("\n" + WARN + "The fixture cycle did not pass cleanly. Fix that "
                            "before spending credits -- it costs nothing to "
                            "retry.")
        if not args.upload:
            return 1

    if args.upload:
        audio = Path(args.upload)
        if not audio.is_file():
            print("Not a file: %s" % audio, file=sys.stderr)
            return 2
        return real_call(audio)

    print("\nIntegration verified without spending a credit. When ready:")
    print("    python3 scripts/probe_api.py --upload data/catalog/human/<a>.mp3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
