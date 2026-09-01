from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Decision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    line: int
    severity: str
    message: str
    suggestion: str


@dataclass(frozen=True)
class BenchmarkResult:
    command: tuple[str, ...]
    samples_seconds: tuple[float, ...]
    median_seconds: float
    mean_seconds: float
    stdev_seconds: float
    min_seconds: float
    max_seconds: float


@dataclass(frozen=True)
class VerificationResult:
    decision: Decision
    speedup_percent: float
    correctness_passed: bool
    stable: bool
    reason: str
    baseline: BenchmarkResult
    candidate: BenchmarkResult

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

