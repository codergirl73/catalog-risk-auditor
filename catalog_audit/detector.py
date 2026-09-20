"""HumanStandard detection client.

=============================================================================
 IF THE API SHAPE IS WRONG, FIX IT HERE AND NOWHERE ELSE
=============================================================================
HumanStandard does not publish open API docs, so the request format and the
response mapping below are a best guess written defensively. Two functions are
the only things that should need changing:

    LiveDetector._build_request()  -- upload format and auth header
    map_response()                 -- their JSON -> (ai_score, confidence)

To see what you actually get back:

    python3 -m catalog_audit.detector path/to/track.mp3

Everything downstream consumes TrackScore and is indifferent to their schema.
=============================================================================

Responses are cached on disk keyed by file hash. Score a catalog once, then
re-run the analysis as often as you like without spending another call, and
demo with the network unplugged.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config
from .models import TrackScore


def sha256_file(path) -> str:
    """Content hash. Doubles as the cache key and the audit trail."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# response mapping -- the part most likely to need a fix on the day
# ---------------------------------------------------------------------------

_AI_KEYS = ("ai_score", "aiScore", "ai_probability", "aiProbability",
            "synthetic_score", "syntheticScore", "ai_likelihood", "score")
_HUMAN_KEYS = ("human_score", "humanScore", "human_probability", "authenticity")
_CONF_KEYS = ("confidence", "confidence_score", "confidenceScore", "certainty")
_NEST_KEYS = ("result", "data", "detection", "analysis", "prediction", "output")


def map_response(payload: dict) -> tuple:
    """Map a HumanStandard response onto (ai_score 0-100, confidence 0-1).

    Walks a list of plausible field names rather than assuming one schema, so
    there is a fair chance it works untouched. Verify it against a real
    response before you rely on it, then delete the branches that do not apply.
    """
    flat = dict(payload)
    for key in _NEST_KEYS:
        inner = payload.get(key)
        if isinstance(inner, dict):
            flat.update(inner)

    ai_score: Optional[float] = None
    for key in _AI_KEYS:
        val = flat.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            ai_score = float(val)
            break

    if ai_score is None:
        for key in _HUMAN_KEYS:
            val = flat.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                v = float(val)
                ai_score = 100.0 - (v * 100.0 if v <= 1.0 else v)
                break

    if ai_score is None:
        label = str(flat.get("label") or flat.get("classification")
                    or flat.get("verdict") or "").lower()
        if any(w in label for w in ("ai", "synthetic", "generated")):
            ai_score = 88.0
        elif any(w in label for w in ("hybrid", "mixed", "assisted")):
            ai_score = 50.0
        elif any(w in label for w in ("human", "authentic", "organic")):
            ai_score = 8.0

    if ai_score is None:
        raise ValueError(
            "No score found in the HumanStandard response. Fix map_response() "
            "in catalog_audit/detector.py. Top-level keys were: "
            + ", ".join(sorted(str(k) for k in flat))
        )

    confidence: Optional[float] = None
    for key in _CONF_KEYS:
        val = flat.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            confidence = float(val)
            break

    if 0.0 <= ai_score <= 1.0:
        ai_score *= 100.0
    if confidence is None:
        confidence = 0.8
    elif confidence > 1.0:
        confidence /= 100.0

    return round(max(0.0, min(100.0, ai_score)), 1), round(
        max(0.0, min(1.0, confidence)), 3)


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

class LiveDetector:
    """The real API, with disk caching and bounded retries."""

    name = "humanstandard"
    is_mock = False

    def __init__(self) -> None:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._last_call = 0.0

    def detect(self, path) -> TrackScore:
        p = Path(path)
        digest = sha256_file(p)

        payload = self._read_cache(digest)
        cached = payload is not None
        if payload is None:
            try:
                payload = self._call_with_retries(p)
            except Exception as exc:
                return TrackScore(
                    filename=p.name, path=str(p), ai_score=-1.0, confidence=0.0,
                    provider=self.name, sha256=digest, error=str(exc),
                )
            self._write_cache(digest, payload)

        try:
            ai_score, confidence = map_response(payload)
        except ValueError as exc:
            return TrackScore(
                filename=p.name, path=str(p), ai_score=-1.0, confidence=0.0,
                provider=self.name, sha256=digest, error=str(exc),
                raw={"response": payload},
            )

        return TrackScore(
            filename=p.name, path=str(p), ai_score=ai_score, confidence=confidence,
            provider=self.name, sha256=digest, cached=cached,
            raw={"response": payload},
        )

    # -- cache -------------------------------------------------------------
    def _cache_file(self, digest: str) -> Path:
        return config.CACHE_DIR / (digest + ".json")

    def _read_cache(self, digest: str):
        f = self._cache_file(digest)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, digest: str, payload: dict) -> None:
        try:
            self._cache_file(digest).write_text(
                json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    # -- transport ---------------------------------------------------------
    def _build_request(self, p: Path) -> urllib.request.Request:
        """Multipart upload. Change the field name or auth header if theirs differ."""
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        body = b"".join([
            ("--%s\r\n" % boundary).encode(),
            ('Content-Disposition: form-data; name="file"; filename="%s"\r\n'
             % p.name).encode(),
            ("Content-Type: %s\r\n\r\n" % ctype).encode(),
            p.read_bytes(),
            ("\r\n--%s--\r\n" % boundary).encode(),
        ])
        req = urllib.request.Request(
            config.HS_API_BASE + config.HS_DETECT_PATH, data=body, method="POST")
        req.add_header("Content-Type",
                       "multipart/form-data; boundary=%s" % boundary)
        req.add_header("Authorization", "Bearer " + config.HS_API_KEY)
        req.add_header("X-API-Key", config.HS_API_KEY)
        req.add_header("Accept", "application/json")
        return req

    def _throttle(self) -> None:
        wait = config.HS_RATE_LIMIT_S - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _call_with_retries(self, p: Path) -> dict:
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(config.HS_MAX_RETRIES + 1):
            try:
                self._throttle()
                return self._call(p)
            except RuntimeError as exc:
                last = exc
                msg = str(exc)
                # Do not burn retries on a request the server will reject again.
                if any(code in msg for code in (" 400:", " 401:", " 403:", " 415:")):
                    raise
                if attempt < config.HS_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
        raise last

    def _call(self, p: Path) -> dict:
        req = self._build_request(p)
        try:
            with urllib.request.urlopen(req, timeout=config.HS_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError("HumanStandard API %s: %s" % (exc.code, detail))
        except Exception as exc:
            raise RuntimeError("HumanStandard API unreachable: %s" % exc)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("Non-JSON response: " + raw[:300])


class MockDetector:
    """Development stand-in. NOT for anything you submit or demo.

    Scores are derived from the file hash, so they are stable per file and
    completely meaningless. It exists only so the pipeline can be exercised
    before a key is in place. Any run using it is marked mock throughout, the
    memo refuses to render without an explicit override, and every output says
    so in plain text.
    """

    name = "mock"
    is_mock = True

    def detect(self, path) -> TrackScore:
        p = Path(path)
        digest = sha256_file(p)
        bucket = int(digest[:8], 16) % 100
        # Confidence sits above the floor only so a mock run exercises all
        # three tiers and you can smoke-test the pipeline before a key
        # arrives. It is as fabricated as the score.
        return TrackScore(
            filename=p.name, path=str(p), ai_score=float(bucket),
            confidence=0.75, provider=self.name, sha256=digest,
            raw={"mock": True, "note": "synthetic value, not a real detection"},
        )


def get_detector(force_mock: bool = False):
    """Live whenever a key exists. Mock only when asked for, or when there is
    no key at all — and it is loud about it either way."""
    if force_mock or not config.HS_API_KEY:
        return MockDetector()
    return LiveDetector()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 -m catalog_audit.detector <audio-file>")
        raise SystemExit(2)

    det = get_detector()
    print("detector: %s" % det.name)
    if det.is_mock:
        print("WARNING: no HS_API_KEY set, this is a meaningless mock value")

    result = det.detect(sys.argv[1])
    print(json.dumps({
        "filename": result.filename,
        "ai_score": result.ai_score,
        "confidence": result.confidence,
        "sha256": result.sha256,
        "cached": result.cached,
        "error": result.error,
        "raw": result.raw,
    }, indent=2)[:6000])
