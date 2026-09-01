from __future__ import annotations

import random
import statistics
from pathlib import Path

from .execution import CommandRunner, ExecutionError, ExecutionPolicy, LocalProcessRunner
from .models import BenchmarkResult, Decision, VerificationResult


def coefficient_of_variation(result: BenchmarkResult) -> float:
    return result.stdev_seconds / result.mean_seconds if result.mean_seconds else 0.0


def speedup_confidence_interval(
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    *,
    resamples: int = 2000,
    seed: int = 42,
) -> tuple[float, float]:
    """Bootstrap median speedup without assuming normally distributed timings."""
    generator = random.Random(seed)
    before_samples = baseline.samples_seconds
    after_samples = candidate.samples_seconds
    if not before_samples or not after_samples:
        return 0.0, 0.0
    estimates: list[float] = []
    for _ in range(resamples):
        before = statistics.median(generator.choices(before_samples, k=len(before_samples)))
        after = statistics.median(generator.choices(after_samples, k=len(after_samples)))
        estimates.append((before - after) / before * 100 if before else 0.0)
    estimates.sort()
    return estimates[int(0.025 * (resamples - 1))], estimates[int(0.975 * (resamples - 1))]


def compare(
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    *,
    correctness_passed: bool,
    minimum_improvement_percent: float = 5.0,
    maximum_cv: float = 0.15,
) -> VerificationResult:
    speedup = (baseline.median_seconds - candidate.median_seconds) / baseline.median_seconds * 100
    confidence_low, confidence_high = speedup_confidence_interval(baseline, candidate)
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
    elif confidence_low < minimum_improvement_percent:
        decision, reason = (
            Decision.INCONCLUSIVE,
            "95% confidence interval does not clear the acceptance threshold",
        )
    else:
        decision, reason = Decision.ACCEPT, "candidate is correct, stable, and measurably faster"
    return VerificationResult(
        decision,
        speedup,
        correctness_passed,
        stable,
        reason,
        baseline,
        candidate,
        confidence_low,
        confidence_high,
    )


def run_correctness(
    command: list[str],
    *,
    cwd: Path,
    timeout: float = 120.0,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
) -> bool:
    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy(timeout_seconds=timeout, cpu_seconds=120)
    try:
        result = selected_runner.run(command, cwd=cwd, policy=selected_policy)
    except (ExecutionError, OSError):
        return False
    return result.returncode == 0
