from __future__ import annotations

import subprocess
from pathlib import Path

from .models import BenchmarkResult, Decision, VerificationResult


def coefficient_of_variation(result: BenchmarkResult) -> float:
    return result.stdev_seconds / result.mean_seconds if result.mean_seconds else 0.0


def compare(
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    *,
    correctness_passed: bool,
    minimum_improvement_percent: float = 5.0,
    maximum_cv: float = 0.15,
) -> VerificationResult:
    speedup = (baseline.median_seconds - candidate.median_seconds) / baseline.median_seconds * 100
    maximum_observed_cv = max(
        coefficient_of_variation(baseline), coefficient_of_variation(candidate)
    )
    stable = maximum_observed_cv <= maximum_cv
    if not correctness_passed:
        decision, reason = Decision.REJECT, "candidate failed the correctness command"
    elif not stable:
        decision, reason = Decision.INCONCLUSIVE, "benchmark variance is too high"
    elif speedup < minimum_improvement_percent:
        decision, reason = Decision.REJECT, "measured improvement is below the acceptance threshold"
    else:
        decision, reason = Decision.ACCEPT, "candidate is correct, stable, and measurably faster"
    return VerificationResult(
        decision, speedup, correctness_passed, stable, reason, baseline, candidate
    )


def run_correctness(command: list[str], *, cwd: Path, timeout: float = 120.0) -> bool:
    try:
        result = subprocess.run(command, cwd=cwd, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0
