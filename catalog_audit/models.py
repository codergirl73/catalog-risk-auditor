"""Core data structures for the catalog audit.

Everything here is a plain dataclass with no external dependencies, so the
whole pipeline stays inspectable and easy to serialize.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class Tier(str, Enum):
    """Where an asset lands after detection.

    CONTESTED is the important one. It is not a hedge, it is a statement that
    audio alone cannot settle the question and a person has to listen.
    """

    CLEAN = "clean"
    CONTESTED = "contested"
    SUSPECT = "suspect"
    ERROR = "error"


@dataclass
class TrackScore:
    """One detection result for one audio file."""

    filename: str
    path: str
    ai_score: float           # 0-100, higher means more likely AI-generated
    confidence: float         # 0-1, the detector's own confidence
    provider: str
    sha256: str = ""
    duration_s: float = 0.0
    cached: bool = False
    error: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class Royalty:
    """One row of the seller's reported earnings."""

    filename: str
    title: str = ""
    artist: str = ""
    annual_usd: float = 0.0


@dataclass
class Asset:
    """A track plus everything the audit learned about it."""

    filename: str
    score: Optional[TrackScore] = None
    royalty: Optional[Royalty] = None
    tier: Tier = Tier.ERROR
    truth: str = ""            # "human" | "ai" | "" when unlabelled
    notes: list = field(default_factory=list)

    @property
    def annual_usd(self) -> float:
        return self.royalty.annual_usd if self.royalty else 0.0

    @property
    def ai_score(self) -> float:
        return self.score.ai_score if self.score and self.score.ok else -1.0


@dataclass
class Valuation:
    """The buyer-facing numbers."""

    track_count: int
    annual_revenue_usd: float
    multiple: float
    asking_price_usd: float

    clean_count: int = 0
    contested_count: int = 0
    suspect_count: int = 0
    error_count: int = 0

    suspect_revenue_usd: float = 0.0
    contested_revenue_usd: float = 0.0

    suspect_revenue_share: float = 0.0      # 0-1
    contested_revenue_share: float = 0.0    # 0-1

    recommended_escrow_usd: float = 0.0
    escrow_basis: str = ""

    @property
    def suspect_share_by_count(self) -> float:
        return self.suspect_count / self.track_count if self.track_count else 0.0


@dataclass
class Evaluation:
    """How well the audit did against planted ground truth.

    Only meaningful when the catalog was constructed with known labels. Real
    acquisitions have no answer key, which is exactly why reporting the error
    rate on a constructed one matters.
    """

    labelled: int = 0
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    contested_ai: int = 0        # AI tracks parked in the contested band
    contested_human: int = 0     # human tracks parked in the contested band
    false_positive_files: list = field(default_factory=list)
    false_negative_files: list = field(default_factory=list)

    @property
    def precision(self) -> Optional[float]:
        d = self.true_positive + self.false_positive
        return self.true_positive / d if d else None

    @property
    def recall(self) -> Optional[float]:
        d = self.true_positive + self.false_negative
        return self.true_positive / d if d else None


@dataclass
class AuditResult:
    """Everything the agent produced for one catalog."""

    catalog_name: str
    catalog_dir: str
    assets: list = field(default_factory=list)
    valuation: Optional[Valuation] = None
    evaluation: Optional[Evaluation] = None
    review_queue: list = field(default_factory=list)
    provider: str = ""
    mock_mode: bool = False
    budget: Optional[Budget] = None
    manifest_sha256: str = ""
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        out: dict[str, Any] = asdict(self)
        for a in out.get("assets", []):
            t = a.get("tier")
            a["tier"] = t.value if hasattr(t, "value") else str(t)
        return out


@dataclass
class Budget:
    """A hard ceiling on live API calls, and a record of what was spent.

    Detection credits are finite and a catalog is arbitrarily large, so the
    agent is given an allowance rather than being trusted to stop on its own.
    When the allowance runs out the remaining assets are recorded as unscored
    -- never as clean. An asset nobody paid to check is not an asset anybody
    verified.
    """

    limit: int
    spent: int = 0
    served_from_cache: int = 0
    skipped: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit

    def can_spend(self) -> bool:
        return self.spent < self.limit

    def spend(self) -> None:
        self.spent += 1

    def note_cached(self) -> None:
        self.served_from_cache += 1

    def note_skipped(self) -> None:
        self.skipped += 1


@dataclass
class Event:
    """Streamed to the terminal so the audience watches the agent work."""

    type: str          # plan | step | tool_call | tool_result | finding | verdict | warn | done
    title: str = ""
    detail: str = ""
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
