from __future__ import annotations

import os
import statistics
import subprocess
import time
from pathlib import Path

from .models import BenchmarkResult


class BenchmarkError(RuntimeError):
    pass


def run_benchmark(
    command: list[str], *, cwd: Path, rounds: int = 7, warmups: int = 2, timeout: float = 30.0
) -> BenchmarkResult:
    if rounds < 3:
        raise ValueError("rounds must be at least 3")
    if warmups < 0:
        raise ValueError("warmups cannot be negative")

    env = {**os.environ, "PYTHONHASHSEED": "0"}
    samples: list[float] = []
    for iteration in range(warmups + rounds):
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BenchmarkError(f"benchmark timed out after {timeout:g}s") from exc
        elapsed = time.perf_counter() - started
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace")[-1000:]
            raise BenchmarkError(f"command exited with {completed.returncode}: {error}")
        if iteration >= warmups:
            samples.append(elapsed)

    return BenchmarkResult(
        command=tuple(command),
        samples_seconds=tuple(samples),
        median_seconds=statistics.median(samples),
        mean_seconds=statistics.fmean(samples),
        stdev_seconds=statistics.stdev(samples),
        min_seconds=min(samples),
        max_seconds=max(samples),
    )

