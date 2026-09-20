"""HumanStandard detection client.

The integration is written against the published API at docs.hsverify.com:

    POST {base}/api/analyze          multipart file=... -> {"job_id": ...}
    GET  {base}/api/jobs/{id}/status poll until status == "complete"

It is asynchronous. Analysis returns a job id and the verdict is collected by
polling; cold starts are documented at 20-30 seconds. `?detail=full` is
requested by default because `tier_verdicts` -- HumanStandard's three
calibrated operating points -- is what this tool tiers on.

Two things are worth knowing before changing anything here:

  * `verdict` has three values, not two: "ai", "human" and "uncertain". The
    detector itself declines to call some tracks, and that refusal is carried
    through to the buyer rather than being rounded to the nearer answer.

  * `?mock=<scenario>` returns a real-shaped fixture and bills nothing.
    Responses carry "mock": true, which is propagated onto TrackScore so a
    fixture can never end up in a memo. Set HS_MOCK_SCENARIO to use it.

Responses are cached on disk keyed by file hash. Score a catalog once, then
re-run the analysis as often as you like without spending another credit, and
demo with the network unplugged.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import config, net
from .models import Budget, TrackScore

# Hashing block size. Large enough that the syscall overhead disappears,
# small enough that a long track never sits in memory whole.
READ_CHUNK_BYTES = 1 << 16


def sha256_file(path) -> str:
    """Content hash. Doubles as the cache key and the audit trail."""
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK_BYTES), b""):
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


# Scores are reported on 0-100 and confidences on 0-1 throughout, so a
# response giving either as a percentage or a fraction ends up in one place.
_PERCENT = 100.0
_MIDPOINT = 50.0

# What to assume when a response carries a score but no confidence in it.
_ASSUMED_CONFIDENCE = 0.8

# Rate limiting is reported by status code, not by exception type.
HTTP_TOO_MANY_REQUESTS = 429
MAX_RETRY_AFTER_S = 30.0
FALLBACK_RETRY_AFTER_S = 5.0

# Where the label heuristic lands for each family of words. Only reached for a
# response that gives no numeric score at all.
_LABEL_SCORES = (
    (("ai", "synthetic", "generated"), 88.0),
    (("hybrid", "mixed", "assisted"), _MIDPOINT),
    (("human", "authentic", "organic"), 8.0),
)

# The verdict values observed in practice. "suspicious" is not in the
# published docs; "uncertain" is. Both mean the detector declined to call it.
_VERDICTS = ("ai", "human", "uncertain", "suspicious")


def _number(value) -> float | None:
    """Coerce to float, refusing bool.

    `bool` subclasses `int`, so a naive isinstance check turns
    {"score": True} into a score of 1.0 and then into 100.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _first_number(flat: dict, keys) -> float | None:
    for key in keys:
        found = _number(flat.get(key))
        if found is not None:
            return found
    return None


def _to_percent(value: float) -> float:
    """Treat a value in [0, 1] as a fraction and anything above it as already
    being a percentage."""
    return value * _PERCENT if 0.0 <= value <= 1.0 else value


def _flatten(payload: dict) -> dict:
    """Merge one level of plausible nesting up into the top level."""
    flat = dict(payload)
    for key in _NEST_KEYS:
        inner = payload.get(key)
        if isinstance(inner, dict):
            flat.update(inner)
    return flat


def _confidence(flat: dict) -> float:
    """The detector's confidence in its own answer, normalised to 0-1."""
    found = _first_number(flat, _CONF_KEYS)
    if found is None:
        return _ASSUMED_CONFIDENCE
    return found / _PERCENT if found > 1.0 else found


# --- scoring strategies, tried in order -----------------------------------
# Each returns a score already on 0-100, or None to defer to the next.

def _score_from_probability(flat: dict) -> float | None:
    """`ai_probability`: a direct likelihood, present on every live response.

    Preferred over reconstructing a score from verdict plus confidence, which
    is the same quantity seen through a decision and lossier for it.
    """
    found = _number(flat.get("ai_probability"))
    return None if found is None else _to_percent(found)


def _score_from_verdict(flat: dict) -> float | None:
    """Reconstruct a score from the verdict and how sure the detector is.

    Confidence is confidence *in the verdict*, so it pushes away from the
    midpoint in whichever direction the verdict points. An uncertain verdict
    sits at the midpoint whatever its confidence, which is the honest place
    for it.
    """
    verdict = str(flat.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        return None
    raw = _number(flat.get("confidence"))
    if raw is None:
        return None
    conf = max(0.0, min(1.0, raw / _PERCENT if raw > 1.0 else raw))
    if verdict == "ai":
        return _MIDPOINT + conf * _MIDPOINT
    if verdict == "human":
        return _MIDPOINT - conf * _MIDPOINT
    return _MIDPOINT


def _score_from_ai_keys(flat: dict) -> float | None:
    found = _first_number(flat, _AI_KEYS)
    return None if found is None else _to_percent(found)


def _score_from_human_keys(flat: dict) -> float | None:
    """Invert a human-ness score into an AI-ness one."""
    found = _first_number(flat, _HUMAN_KEYS)
    return None if found is None else _PERCENT - _to_percent(found)


def _score_from_label(flat: dict) -> float | None:
    """Last resort: a worded classification with no number attached."""
    label = str(flat.get("label") or flat.get("classification")
                or flat.get("verdict") or "").lower()
    for words, score in _LABEL_SCORES:
        if any(word in label for word in words):
            return score
    return None


_STRATEGIES = (_score_from_probability, _score_from_verdict,
               _score_from_ai_keys, _score_from_human_keys, _score_from_label)


def map_response(payload: dict) -> tuple:
    """Map a HumanStandard response onto (ai_score 0-100, confidence 0-1).

    Tries each known shape in turn rather than assuming one, because the
    published reference and the live API disagree in several places. A
    response matching none of them raises, naming the keys it did carry --
    inventing a score would be worse than failing.
    """
    flat = _flatten(payload)

    for strategy in _STRATEGIES:
        score = strategy(flat)
        if score is not None:
            return (round(max(0.0, min(_PERCENT, score)), 1),
                    round(max(0.0, min(1.0, _confidence(flat))), 3))

    raise ValueError(
        "No score found in the HumanStandard response. Fix map_response() "
        "in catalog_audit/detector.py. Top-level keys were: "
        + ", ".join(sorted(str(k) for k in flat))
    )


# The live API sends risk_segments_full_mix; the docs call it risk_segments;
# the hybrid endpoint adds per-stem variants. Preference order, first wins.
_SEGMENT_KEYS = ("risk_segments_full_mix", "risk_segments",
                 "risk_segments_vocal", "risk_segments_instrumental")

# The wire form is snake_case; the published docs print the display form.
_LABEL_DISPLAY = {"ai_generated": "AI-Generated", "ai_assisted": "AI-Assisted"}


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    return str(value) if isinstance(value, (str, int, float)) else ""


def _decimal(payload: dict, key: str) -> float:
    return _number(payload.get(key)) or 0.0


def _tier_verdicts(payload: dict) -> dict:
    tiers = payload.get("tier_verdicts")
    return {k: str(v).lower() for k, v in tiers.items()} if isinstance(
        tiers, dict) else {}


def _segments(payload: dict) -> list:
    """Per-window risk with real timestamps, from whichever key carries it."""
    for key in _SEGMENT_KEYS:
        raw = payload.get(key)
        if isinstance(raw, list) and raw:
            return [
                {"start": _decimal(seg, "start"),
                 "end": _decimal(seg, "end"),
                 "risk": _decimal(seg, "risk")}
                for seg in raw if isinstance(seg, dict)
            ]
    return []


def _timeline(payload: dict, segments: list) -> list:
    """The flat per-window risk list, derived from segments when absent."""
    raw = payload.get("risk_timeline")
    if isinstance(raw, list):
        found = [_number(x) for x in raw]
        timeline = [x for x in found if x is not None]
        if timeline:
            return timeline
    return [seg["risk"] for seg in segments]


def _label_basis(payload: dict) -> list:
    """Plain-language reasons for the industry label.

    Absent from the published response table entirely, and the best short
    evidence line the API produces, so it is kept.
    """
    basis = payload.get("industry_label_basis")
    if isinstance(basis, str):
        basis = [basis]
    return [str(b) for b in basis] if isinstance(basis, list) else []


def parse_result(payload: dict) -> dict:
    """Pull the fields a buyer-facing report can actually use out of a result.

    Tolerant by design: every field is optional, because a response missing
    `origin_map` should still produce a verdict rather than an exception.
    """
    if not isinstance(payload, dict):
        return {}

    inner = payload.get("result")
    if isinstance(inner, dict):
        payload = {**payload, **inner}

    origin_map = payload.get("origin_map")
    origin_map = origin_map if isinstance(origin_map, dict) else {}

    label = _text(payload, "industry_label")
    label = _LABEL_DISPLAY.get(label.strip().lower(), label)

    segments = _segments(payload)

    return {
        "verdict": _text(payload, "verdict").lower(),
        "tier_verdicts": _tier_verdicts(payload),
        "origin": _text(payload, "origin"),
        "origin_confidence": _decimal(payload, "origin_confidence"),
        "origin_summary": str(origin_map.get("summary_line") or ""),
        "origin_map_evidence": _text(payload, "origin_map_evidence"),
        "headline_verdict": _text(payload, "headline_verdict").lower(),
        "industry_label": label,
        "industry_label_status": _text(payload, "industry_label_status").lower(),
        "industry_label_basis": _label_basis(payload),
        "risk_timeline": _timeline(payload, segments),
        "risk_segments": segments,
        "duration_s": _decimal(payload, "duration_sec"),
        "model_version": _text(payload, "model_version"),
        "mock": payload.get("mock") is True,
        "mock_scenario": _text(payload, "mock_scenario"),
    }


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

class LiveDetector:
    """The real API, with disk caching and bounded retries."""

    name = "humanstandard"
    is_mock = False

    def __init__(self, budget: Budget | None = None) -> None:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._last_call = 0.0
        self.budget = budget

    def detect(self, path, digest: str = "") -> TrackScore:
        p = Path(path)
        digest = digest or sha256_file(p)

        def failed(msg: str) -> TrackScore:
            return TrackScore(
                filename=p.name, path=str(p), ai_score=-1.0, confidence=0.0,
                provider=self.name, sha256=digest, error=msg,
            )

        payload = self._read_cache(digest)
        cached = payload is not None
        if cached and self.budget:
            self.budget.note_cached()

        if payload is None:
            oversized = self._oversized(p)
            if oversized:
                if self.budget:
                    self.budget.note_skipped()
                return failed(oversized)

            # Spend before the call, not after. A request that times out may
            # still have been charged, so the optimistic accounting is the
            # one that overruns the budget.
            if self.budget and not self.budget.can_spend():
                self.budget.note_skipped()
                return failed(
                    "Call budget of %d exhausted. Left unscored rather than "
                    "assumed clean." % self.budget.limit)
            if self.budget:
                self.budget.spend()

            try:
                payload = self._call_with_retries(p)
            except Exception as exc:
                return failed(str(exc))
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
            filename=p.name, path=str(p), ai_score=ai_score,
            confidence=confidence, provider=self.name, sha256=digest,
            cached=cached, raw={"response": payload},
            **parse_result(payload),
        )

    @staticmethod
    def _oversized(p: Path) -> str:
        try:
            mb = p.stat().st_size / 1e6
        except OSError as exc:
            return "Could not read %s: %s" % (p.name, exc)
        if mb > config.HS_MAX_UPLOAD_MB:
            return ("File is %.0f MB, above the %.0f MB upload ceiling. Not "
                    "sent. Likely a mix or a set rather than a track."
                    % (mb, config.HS_MAX_UPLOAD_MB))
        return ""

    # -- cache -------------------------------------------------------------
    def _cache_file(self, digest: str) -> Path:
        return config.CACHE_DIR / (digest + ".json")

    def is_cached(self, digest: str) -> bool:
        return self._cache_file(digest).exists()

    def _read_cache(self, digest: str):
        f = self._cache_file(digest)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, digest: str, payload: dict) -> None:
        with contextlib.suppress(OSError):
            self._cache_file(digest).write_text(
                json.dumps(payload, indent=2), encoding="utf-8")

    # -- transport ---------------------------------------------------------
    @staticmethod
    def base_headers() -> dict:
        """Headers every request carries, auth included.

        The User-Agent is not decoration. Cloudflare sits in front of the API
        and rejects urllib's default agent with a 403 and "error code: 1010",
        which is indistinguishable from a bad key until you read the body.
        """
        headers = {
            "Accept": "application/json",
            "User-Agent": config.HS_USER_AGENT,
        }
        headers.update(LiveDetector.auth_headers())
        return headers

    @staticmethod
    def auth_headers() -> dict:
        """Present the key the way HS_AUTH_STYLE says to.

        The default sends two headers at once, which is the right opening move
        when the scheme is unknown but the wrong thing to leave in place: some
        gateways reject a request carrying conflicting credentials. Once
        scripts/probe_api.py has established which one works, set HS_AUTH_STYLE
        in .env and only that header goes out.
        """
        key = config.HS_API_KEY
        style = config.HS_AUTH_STYLE
        if style == "bearer":
            return {"Authorization": "Bearer " + key}
        if style == "x-api-key":
            return {"X-API-Key": key}
        if style == "api-key":
            return {"Api-Key": key}
        if style == "authorization-raw":
            return {"Authorization": key}
        return {"Authorization": "Bearer " + key, "X-API-Key": key}

    def _query(self, extra: dict | None = None) -> str:
        """Query string shared by analyze and status."""
        params = {}
        if config.HS_DETAIL:
            params["detail"] = config.HS_DETAIL
        if config.HS_MOCK_SCENARIO:
            params["mock"] = config.HS_MOCK_SCENARIO
        params.update(extra or {})
        return ("?" + urllib.parse.urlencode(params)) if params else ""

    def _build_request(self, p: Path) -> urllib.request.Request:
        """Multipart upload. Field name and auth style both come from config."""
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        body = b"".join([
            ("--%s\r\n" % boundary).encode(),
            ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
             % (config.HS_FILE_FIELD, p.name)).encode(),
            ("Content-Type: %s\r\n\r\n" % ctype).encode(),
            p.read_bytes(),
            ("\r\n--%s--\r\n" % boundary).encode(),
        ])
        url = config.HS_API_BASE + config.HS_DETECT_PATH + self._query()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type",
                       "multipart/form-data; boundary=%s" % boundary)
        for name, value in self.base_headers().items():
            req.add_header(name, value)
        return req

    def _status_request(self, job_id: str) -> urllib.request.Request:
        path = config.HS_STATUS_PATH.replace(
            "{job_id}", urllib.parse.quote(str(job_id), safe=""))
        req = urllib.request.Request(
            config.HS_API_BASE + path + self._query(), method="GET")
        for name, value in self.base_headers().items():
            req.add_header(name, value)
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
                return self._call(p)
            except RuntimeError as exc:
                last = exc
                msg = str(exc)
                # Do not burn retries on a request the server will reject
                # again on its merits.
                if any(code in msg for code in
                       (" 400:", " 401:", " 403:", " 415:", " 422:")):
                    raise
                if attempt < config.HS_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
        raise last

    def _call(self, p: Path) -> dict:
        """Submit, then poll until the verdict lands."""
        submitted = self._fetch(self._build_request(p))

        job_id = submitted.get("job_id") or submitted.get("id")
        if not job_id:
            # Some endpoints answer synchronously. If a verdict already came
            # back, take it rather than insisting on a job id.
            if submitted.get("verdict") or submitted.get("result"):
                return submitted
            raise RuntimeError(
                "No job_id in the analyze response. Keys were: "
                + ", ".join(sorted(str(k) for k in submitted)))

        return self._poll(job_id)

    def _poll(self, job_id: str) -> dict:
        """Poll a job to completion.

        Cold starts are documented at 20-30s, so the timeout is generous and
        the interval is not. A job that never completes raises rather than
        returning something half-finished.
        """
        deadline = time.monotonic() + config.HS_POLL_TIMEOUT_S
        last_status = "unknown"
        while time.monotonic() < deadline:
            payload = self._fetch(self._status_request(job_id))
            last_status = str(payload.get("status") or "").lower()

            if last_status == "complete":
                result = payload.get("result")
                return result if isinstance(result, dict) else payload
            if last_status == "failed":
                raise RuntimeError(
                    "Analysis failed for job %s: %s"
                    % (job_id, payload.get("error") or "no reason given"))

            time.sleep(config.HS_POLL_INTERVAL_S)

        raise RuntimeError(
            "Job %s did not complete within %.0fs (last status: %s)"
            % (job_id, config.HS_POLL_TIMEOUT_S, last_status))

    def _fetch(self, req: urllib.request.Request) -> dict:
        """Every request goes through here, so every request is throttled.

        Polling is requests too. Rate-limiting only the uploads would have let
        a 116-track run issue several hundred unthrottled status checks.
        """
        self._throttle()
        try:
            with net.urlopen(req, timeout=config.HS_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code == HTTP_TOO_MANY_REQUESTS:
                # Respect Retry-After when they send one, then let the caller
                # retry rather than sleeping inside a request.
                wait = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    time.sleep(min(MAX_RETRY_AFTER_S, float(wait)))
                except (TypeError, ValueError):
                    time.sleep(FALLBACK_RETRY_AFTER_S)
            raise RuntimeError(
                "HumanStandard API %s: %s" % (exc.code, detail)) from exc
        except Exception as exc:
            raise RuntimeError(
                "HumanStandard API unreachable: %s" % exc) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("Non-JSON response: " + raw[:300]) from None


# Hash buckets the mock detector uses to land a file in each tier. These
# are arbitrary by design -- the scores are fabricated and say so.
_PERCENT_BUCKETS = 100
_MOCK_CONFIDENT_AI = 78
_MOCK_LIKELY_AI = 62
_MOCK_BORDERLINE = 40


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

    def __init__(self, budget: Budget | None = None) -> None:
        self.budget = budget

    def is_cached(self, digest: str) -> bool:
        return False

    def detect(self, path, digest: str = "") -> TrackScore:
        p = Path(path)
        digest = digest or sha256_file(p)

        # The mock spends the budget too. A guard that is only exercised when
        # real money is on the line is a guard nobody has ever seen work.
        if self.budget and not self.budget.can_spend():
            self.budget.note_skipped()
            return TrackScore(
                filename=p.name, path=str(p), ai_score=-1.0, confidence=0.0,
                provider=self.name, sha256=digest,
                error="Call budget of %d exhausted. Left unscored rather than "
                      "assumed clean." % self.budget.limit,
            )
        if self.budget:
            self.budget.spend()

        bucket = int(digest[:8], 16) % _PERCENT_BUCKETS

        # Shape the fixture like a real response, including tier_verdicts.
        # Without them every local run would exercise the score-band fallback
        # and the calibrated path -- the one that actually ships -- would be
        # covered only by unit tests. The numbers remain fabricated.
        if bucket > _MOCK_CONFIDENT_AI:
            verdict, tiers = "ai", ("ai", "ai", "ai")
        elif bucket > _MOCK_LIKELY_AI:
            verdict, tiers = "ai", ("uncertain", "ai", "ai")
        elif bucket > _MOCK_BORDERLINE:
            verdict, tiers = "uncertain", ("uncertain", "human", "ai")
        else:
            verdict, tiers = "human", ("human", "human", "human")

        return TrackScore(
            filename=p.name, path=str(p), ai_score=float(bucket),
            confidence=0.75, provider=self.name, sha256=digest,
            verdict=verdict,
            tier_verdicts=dict(zip(
                ("press_safe", "human_safe", "recall"), tiers,
                strict=True)),
            origin="suno" if verdict == "ai" else "",
            origin_summary=("fabricated attribution, not a real detection"
                            if verdict == "ai" else ""),
            raw={"mock": True, "note": "synthetic value, not a real detection"},
        )


def get_detector(force_mock: bool = False, budget: Budget | None = None):
    """Live whenever a key exists. Mock only when asked for, or when there is
    no key at all — and it is loud about it either way."""
    if force_mock or not config.HS_API_KEY:
        return MockDetector(budget=budget)
    return LiveDetector(budget=budget)


# argv[0] is the module; argv[1] is the audio file this expects.
_ARGV_WITH_AUDIO = 2

if __name__ == "__main__":
    if len(sys.argv) < _ARGV_WITH_AUDIO:
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
