import sys
from pathlib import Path

from perf_engineer.benchmark import run_paired_benchmarks


def test_paired_benchmark_collects_equal_samples(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    command = [sys.executable, "-c", "pass"]
    before, after = run_paired_benchmarks(
        command, baseline_cwd=baseline, candidate_cwd=candidate, rounds=3, warmups=0
    )
    assert len(before.samples_seconds) == len(after.samples_seconds) == 3
