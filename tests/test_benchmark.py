from pathlib import Path

import pytest

from perf_engineer.benchmark import run_adaptive_paired_benchmarks
from perf_engineer.execution import ExecutionPolicy, ExecutionResult


class SequenceRunner:
    def __init__(self, baseline_seconds: float, candidate_seconds: float) -> None:
        self.baseline_seconds = baseline_seconds
        self.candidate_seconds = candidate_seconds
        self.calls = {"baseline": 0, "candidate": 0}

    def run(
        self, command: list[str], *, cwd: Path, policy: ExecutionPolicy
    ) -> ExecutionResult:
        name = cwd.name
        self.calls[name] += 1
        seconds = (
            self.baseline_seconds if name == "baseline" else self.candidate_seconds
        )
        return ExecutionResult(0, seconds, seconds, 1024, "")


def test_adaptive_benchmark_repeats_short_commands(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    runner = SequenceRunner(0.02, 0.01)

    before, after = run_adaptive_paired_benchmarks(
        ["work"],
        baseline_cwd=baseline,
        candidate_cwd=candidate,
        minimum_rounds=3,
        maximum_rounds=3,
        warmups=0,
        minimum_sample_seconds=0.1,
        minimum_measurement_seconds=0.0,
        runner=runner,
    )

    assert runner.calls["baseline"] == 16
    assert runner.calls["candidate"] == 15
    assert before.median_seconds == 0.1
    assert after.median_seconds == 0.05
    assert before.calibration_probe_seconds == 0.02
    assert before.repetitions_per_sample == 5
    assert before.measurement_rounds == 3
    assert before.total_measurement_seconds == pytest.approx(0.45)
    assert after.repetitions_per_sample == 5
    assert after.measurement_rounds == 3


def test_adaptive_benchmark_keeps_long_commands_single_shot(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    runner = SequenceRunner(0.2, 0.15)

    before, after = run_adaptive_paired_benchmarks(
        ["work"],
        baseline_cwd=baseline,
        candidate_cwd=candidate,
        minimum_rounds=3,
        maximum_rounds=3,
        warmups=0,
        minimum_sample_seconds=0.1,
        minimum_measurement_seconds=0.0,
        runner=runner,
    )

    assert runner.calls["baseline"] == 4
    assert runner.calls["candidate"] == 3
    assert before.calibration_probe_seconds == 0.2
    assert before.repetitions_per_sample == 1
    assert before.measurement_rounds == 3
    assert after.repetitions_per_sample == 1
