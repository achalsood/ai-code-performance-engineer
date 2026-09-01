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
    cpu_mean_seconds: float = 0.0
    peak_memory_bytes: int = 0


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


@dataclass(frozen=True)
class ExperimentRecord:
    schema_version: int
    experiment_id: str
    created_at: str
    repository: str
    baseline_ref: str
    baseline_commit: str
    candidate_ref: str
    candidate_commit: str
    benchmark_command: tuple[str, ...]
    test_command: tuple[str, ...]
    result: VerificationResult

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
