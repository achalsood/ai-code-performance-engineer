from __future__ import annotations

import statistics
from pathlib import Path

from .execution import CommandRunner, ExecutionError, ExecutionPolicy, LocalProcessRunner
from .models import BenchmarkResult


class BenchmarkError(RuntimeError):
    pass


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
        try:
            completed = selected_runner.run(command, cwd=cwd, policy=selected_policy)
        except ExecutionError as exc:
            raise BenchmarkError(str(exc)) from exc
        if completed.returncode != 0:
            raise BenchmarkError(
                f"command exited with {completed.returncode}: {completed.stderr}"
            )
        if iteration >= warmups:
            samples.append(completed.wall_seconds)
            cpu_samples.append(completed.cpu_seconds)
            peak_memory_bytes = max(peak_memory_bytes, completed.peak_memory_bytes)

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
