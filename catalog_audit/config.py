"""Configuration. Env-driven, with every threshold explicit and printable.

The agent shows these numbers on screen during a run. Stated thresholds can be
argued with; hidden ones just have to be trusted.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Tiny .env reader so the project needs no packages at all."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- HumanStandard API ------------------------------------------------------
HS_API_KEY = os.environ.get("HS_API_KEY", "").strip()
# NB: api.hsverify.com does not resolve -- confirmed by scripts/probe_api.py.
# The real base URL has to come from HumanStandard. Set HS_API_BASE in .env.
HS_API_BASE = os.environ.get("HS_API_BASE", "https://api.hsverify.com").rstrip("/")
HS_DETECT_PATH = os.environ.get("HS_DETECT_PATH", "/v1/detect")
HS_TIMEOUT_S = _f("HS_TIMEOUT_S", 60.0)
HS_MAX_RETRIES = int(_f("HS_MAX_RETRIES", 2))
HS_RATE_LIMIT_S = _f("HS_RATE_LIMIT_S", 0.35)   # polite pause between calls

# Hard ceiling on live detection calls for one run. The hackathon key is
# issued with 200 credits, so an unguarded run over a large catalog could
# spend every one of them before anybody noticed. The agent estimates its
# spend against this before it starts and stops when it is reached.
HS_CREDIT_BUDGET = int(_f("HS_CREDIT_BUDGET", 200))

# Refuse to upload anything larger than this. A DJ set or a radio show
# that wandered into the catalog folder would otherwise burn a credit and
# a long timeout to tell us nothing useful.
HS_MAX_UPLOAD_MB = _f("HS_MAX_UPLOAD_MB", 20.0)

# How the key is presented and what the upload field is called. Both are
# settable from .env so that whatever scripts/probe_api.py discovers about the
# real API becomes a config change rather than a code change.
#   bearer | x-api-key | api-key | authorization-raw | both
HS_AUTH_STYLE = os.environ.get("HS_AUTH_STYLE", "both").strip().lower()
HS_FILE_FIELD = os.environ.get("HS_FILE_FIELD", "file").strip() or "file"

CACHE_DIR = Path(os.environ.get("HS_CACHE_DIR", str(ROOT / ".cache")))

# --- Tier thresholds --------------------------------------------------------
# Below CLEAN_CEILING: treated as human. Above SUSPECT_FLOOR: treated as
# at-risk. Everything between is contested and goes to a person.
CLEAN_CEILING = _f("CLEAN_CEILING", 25.0)
SUSPECT_FLOOR = _f("SUSPECT_FLOOR", 65.0)

# A detector result below this confidence is pushed into the contested band
# regardless of its score.
MIN_CONFIDENCE = _f("MIN_CONFIDENCE", 0.60)

# --- Valuation --------------------------------------------------------------
# Catalogs are commonly priced at a multiple of annual royalty income.
DEFAULT_MULTIPLE = _f("DEFAULT_MULTIPLE", 15.0)

# Share of contested revenue folded into the escrow recommendation. Suspect
# revenue is escrowed in full; contested revenue is genuinely uncertain, so
# only part of it is held back.
CONTESTED_ESCROW_WEIGHT = _f("CONTESTED_ESCROW_WEIGHT", 0.5)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aif", ".aiff", ".ogg"}

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "out")))


def thresholds_summary() -> str:
    return (
        f"clean < {CLEAN_CEILING:.0f} | contested {CLEAN_CEILING:.0f}-"
        f"{SUSPECT_FLOOR:.0f} | suspect > {SUSPECT_FLOOR:.0f} | "
        f"min confidence {MIN_CONFIDENCE:.2f}"
    )


def budget_summary(needed: int) -> str:
    return (
        f"{needed} live call{'' if needed == 1 else 's'} needed, "
        f"{HS_CREDIT_BUDGET} credit budget"
    )
