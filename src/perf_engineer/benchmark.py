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
    command: list[str], samples: list[float], cpu_samples: list[float], peak_memory_bytes: int
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
