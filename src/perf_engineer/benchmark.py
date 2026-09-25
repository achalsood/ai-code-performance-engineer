from __future__ import annotations

import statistics
from pathlib import Path

from .execution import (
    CommandRunner,
    ExecutionError,
    ExecutionPolicy,
    ExecutionResult,
    LocalProcessRunner,
)
from .models import BenchmarkResult


class BenchmarkError(RuntimeError):
    pass


def _summarize(
    command: list[str],
    samples: list[float],
    cpu_samples: list[float],
    peak_memory_bytes: int,
    *,
    calibration_probe_seconds: float | None = None,
    repetitions_per_sample: int = 1,
    total_measurement_seconds: float | None = None,
) -> BenchmarkResult:
    return BenchmarkResult(
        command=tuple(command),
        samples_seconds=tuple(samples),
        median_seconds=statistics.median(samples),
        mean_seconds=statistics.fmean(samples),
        stdev_seconds=statistics.stdev(samples),
        min_seconds=min(samples),
        max_seconds=max(samples),
        cpu_mean_seconds=statistics.fmean(cpu_samples),
        peak_memory_bytes=peak_memory_bytes,
        calibration_probe_seconds=calibration_probe_seconds,
        repetitions_per_sample=repetitions_per_sample,
        measurement_rounds=len(samples),
        total_measurement_seconds=total_measurement_seconds,
    )


def _measure_once(
    command: list[str], cwd: Path, runner: CommandRunner, policy: ExecutionPolicy
) -> ExecutionResult:
    try:
        completed = runner.run(command, cwd=cwd, policy=policy)
    except ExecutionError as exc:
        raise BenchmarkError(str(exc)) from exc
    if completed.returncode != 0:
        raise BenchmarkError(f"command exited with {completed.returncode}: {completed.stderr}")
    return completed


def run_benchmark(
    command: list[str],
    *,
    cwd: Path,
    rounds: int = 7,
    warmups: int = 2,
    timeout: float = 30.0,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
) -> BenchmarkResult:
    if rounds < 3:
        raise ValueError("rounds must be at least 3")
    if warmups < 0:
        raise ValueError("warmups cannot be negative")

    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy(timeout_seconds=timeout)
    samples: list[float] = []
    cpu_samples: list[float] = []
    peak_memory_bytes = 0
    for iteration in range(warmups + rounds):
        completed = _measure_once(command, cwd, selected_runner, selected_policy)
        if iteration >= warmups:
            samples.append(completed.wall_seconds)
            cpu_samples.append(completed.cpu_seconds)
            peak_memory_bytes = max(peak_memory_bytes, completed.peak_memory_bytes)

    return _summarize(command, samples, cpu_samples, peak_memory_bytes)


def run_paired_benchmarks(
    command: list[str],
    *,
    baseline_cwd: Path,
    candidate_cwd: Path,
    rounds: int = 7,
    warmups: int = 2,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
) -> tuple[BenchmarkResult, BenchmarkResult]:
    """Alternate AB/BA execution order to reduce temporal and thermal bias."""
    if rounds < 3:
        raise ValueError("rounds must be at least 3")
    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy()
    for directory in (baseline_cwd, candidate_cwd):
        for _ in range(warmups):
            _measure_once(command, directory, selected_runner, selected_policy)
    samples: dict[str, list[float]] = {"baseline": [], "candidate": []}
    cpu: dict[str, list[float]] = {"baseline": [], "candidate": []}
    memory = {"baseline": 0, "candidate": 0}
    directories = {"baseline": baseline_cwd, "candidate": candidate_cwd}
    for round_index in range(rounds):
        order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
        for name in order:
            result = _measure_once(command, directories[name], selected_runner, selected_policy)
            samples[name].append(result.wall_seconds)
            cpu[name].append(result.cpu_seconds)
            memory[name] = max(memory[name], result.peak_memory_bytes)
    return (
        _summarize(command, samples["baseline"], cpu["baseline"], memory["baseline"]),
        _summarize(command, samples["candidate"], cpu["candidate"], memory["candidate"]),
    )




def _bootstrap_median_interval(
    values: list[float], *, resamples: int = 1000, seed: int = 42
) -> tuple[float, float]:
    """Return a deterministic bootstrap interval for a sample median."""
    if not values:
        return 0.0, 0.0
    import random

    generator = random.Random(seed)
    estimates = [
        statistics.median(generator.choices(values, k=len(values))) for _ in range(resamples)
    ]
    estimates.sort()
    return (
        estimates[int(0.025 * (resamples - 1))],
        estimates[int(0.975 * (resamples - 1))],
    )

def run_adaptive_paired_benchmarks(
    command: list[str],
    *,
    baseline_cwd: Path,
    candidate_cwd: Path,
    minimum_rounds: int = 7,
    maximum_rounds: int = 21,
    warmups: int = 2,
    target_mad_percent: float = 1.5,
    minimum_sample_seconds: float = 0.1,
    minimum_measurement_seconds: float = 1.0,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
) -> tuple[BenchmarkResult, BenchmarkResult]:
    """Run matched AB/BA trials until effects are stable and sufficiently observed."""
    if minimum_rounds < 3 or maximum_rounds < minimum_rounds:
        raise ValueError("adaptive rounds require 3 <= minimum_rounds <= maximum_rounds")
    if min(minimum_sample_seconds, minimum_measurement_seconds) < 0:
        raise ValueError("adaptive measurement durations cannot be negative")

    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy()
    directories = {"baseline": baseline_cwd, "candidate": candidate_cwd}

    # Calibrate measurement effort to the current machine. Short commands are
    # repeated within each sample so process/scheduler noise is a smaller share
    # of the measured work; naturally long commands remain single-shot.
    probe = _measure_once(command, baseline_cwd, selected_runner, selected_policy)
    repetitions = max(
        1,
        min(
            32,
            int(minimum_sample_seconds / max(probe.wall_seconds, 1e-9) + 0.999999),
        ),
    )

    def measure(name: str) -> ExecutionResult:
        results = [
            _measure_once(command, directories[name], selected_runner, selected_policy)
            for _ in range(repetitions)
        ]
        return ExecutionResult(
            returncode=0,
            wall_seconds=sum(item.wall_seconds for item in results),
            cpu_seconds=sum(item.cpu_seconds for item in results),
            peak_memory_bytes=max(item.peak_memory_bytes for item in results),
            stderr="",
        )

    for name in ("baseline", "candidate"):
        for _ in range(warmups):
            measure(name)

    samples: dict[str, list[float]] = {"baseline": [], "candidate": []}
    cpu: dict[str, list[float]] = {"baseline": [], "candidate": []}
    memory = {"baseline": 0, "candidate": 0}
    measured_seconds = 0.0

    for round_index in range(maximum_rounds):
        order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
        round_results: dict[str, ExecutionResult] = {}
        for name in order:
            result = measure(name)
            round_results[name] = result
            samples[name].append(result.wall_seconds)
            cpu[name].append(result.cpu_seconds)
            memory[name] = max(memory[name], result.peak_memory_bytes)
        measured_seconds += sum(item.wall_seconds for item in round_results.values())

        if round_index + 1 >= minimum_rounds and measured_seconds >= minimum_measurement_seconds:
            effects = [
                (before - after) / before * 100 if before else 0.0
                for before, after in zip(samples["baseline"], samples["candidate"], strict=True)
            ]
            center = statistics.median(effects)
            mad = statistics.median(abs(effect - center) for effect in effects)
            confidence_low, _ = _bootstrap_median_interval(effects)
            # Stability alone is not enough to stop sampling when the result is
            # still statistically ambiguous. Keep collecting paired evidence
            # while the robust interval straddles a practically relevant gain.
            if mad <= target_mad_percent and confidence_low > 0.0:
                break

    return (
        _summarize(
            command,
            samples["baseline"],
            cpu["baseline"],
            memory["baseline"],
            calibration_probe_seconds=probe.wall_seconds,
            repetitions_per_sample=repetitions,
            total_measurement_seconds=measured_seconds,
        ),
        _summarize(
            command,
            samples["candidate"],
            cpu["candidate"],
            memory["candidate"],
            calibration_probe_seconds=probe.wall_seconds,
            repetitions_per_sample=repetitions,
            total_measurement_seconds=measured_seconds,
        ),
    )
