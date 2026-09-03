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


def paired_speedup_confidence_interval(
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    *,
    resamples: int = 2000,
    seed: int = 42,
) -> tuple[float, float]:
    """Bootstrap matched AB/BA trial ratios, preserving environmental pairing."""
    if len(baseline.samples_seconds) != len(candidate.samples_seconds):
        raise ValueError("paired benchmarks must contain the same number of samples")
    paired = [
        (before - after) / before * 100 if before else 0.0
        for before, after in zip(baseline.samples_seconds, candidate.samples_seconds, strict=True)
    ]
    if not paired:
        return 0.0, 0.0
    generator = random.Random(seed)
    estimates = [
        statistics.median(generator.choices(paired, k=len(paired))) for _ in range(resamples)
    ]
    estimates.sort()
    return estimates[int(0.025 * (resamples - 1))], estimates[int(0.975 * (resamples - 1))]


def paired_effect_mad(baseline: BenchmarkResult, candidate: BenchmarkResult) -> float:
    """Return robust dispersion of matched speedup percentages."""
    effects = [
        (before - after) / before * 100 if before else 0.0
        for before, after in zip(baseline.samples_seconds, candidate.samples_seconds, strict=True)
    ]
    center = statistics.median(effects)
    return statistics.median(abs(effect - center) for effect in effects)


def compare(
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    *,
    correctness_passed: bool,
    minimum_improvement_percent: float = 5.0,
    maximum_cv: float = 0.15,
    paired: bool = False,
    maximum_paired_mad_percent: float = 5.0,
    minimum_absolute_improvement_seconds: float = 0.001,
    maximum_memory_regression_percent: float = 10.0,
    maximum_cpu_regression_percent: float = 10.0,
) -> VerificationResult:
    speedup = (baseline.median_seconds - candidate.median_seconds) / baseline.median_seconds * 100
    confidence_method = (
        paired_speedup_confidence_interval if paired else speedup_confidence_interval
    )
    confidence_low, confidence_high = confidence_method(baseline, candidate)
    absolute_improvement = baseline.median_seconds - candidate.median_seconds
    memory_change = (
        (candidate.peak_memory_bytes - baseline.peak_memory_bytes)
        / baseline.peak_memory_bytes
        * 100
        if baseline.peak_memory_bytes
        else 0.0
    )
    cpu_change = (
        (candidate.cpu_mean_seconds - baseline.cpu_mean_seconds) / baseline.cpu_mean_seconds * 100
        if baseline.cpu_mean_seconds
        else 0.0
    )
    stable = (
        paired_effect_mad(baseline, candidate) <= maximum_paired_mad_percent
        if paired
        else max(coefficient_of_variation(baseline), coefficient_of_variation(candidate))
        <= maximum_cv
    )
    if not correctness_passed:
        decision, reason = Decision.REJECT, "candidate failed the correctness command"
    elif not stable:
        decision, reason = Decision.INCONCLUSIVE, "benchmark variance is too high"
    elif memory_change > maximum_memory_regression_percent:
        decision, reason = Decision.REJECT, "candidate exceeds the memory regression budget"
    elif cpu_change > maximum_cpu_regression_percent:
        decision, reason = Decision.REJECT, "candidate exceeds the CPU regression budget"
    elif absolute_improvement < minimum_absolute_improvement_seconds:
        decision, reason = Decision.REJECT, "absolute runtime improvement is too small"
    elif speedup < minimum_improvement_percent:
        decision, reason = Decision.REJECT, "measured improvement is below the acceptance threshold"
    elif confidence_low < minimum_improvement_percent:
        decision, reason = (
            Decision.INCONCLUSIVE,
            "95% confidence interval does not clear the acceptance threshold",
        )
    else:
        decision, reason = Decision.ACCEPT, "candidate is correct, stable, and measurably faster"
    utility_score = confidence_low - max(memory_change, 0.0) * 0.25 - max(cpu_change, 0.0) * 0.25
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
        memory_change,
        cpu_change,
        utility_score,
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
