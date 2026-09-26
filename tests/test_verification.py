import pytest

from perf_engineer.models import BenchmarkResult, Decision
from perf_engineer.verification import compare


def result(median: float, stdev: float = 0.001) -> BenchmarkResult:
    return BenchmarkResult(("work",), (median,) * 5, median, median, stdev, median, median)


def test_accepts_correct_stable_improvement() -> None:
    verdict = compare(result(1.0), result(0.7), correctness_passed=True)
    assert verdict.decision is Decision.ACCEPT
    assert round(verdict.speedup_percent) == 30


def test_rejects_incorrect_candidate_even_when_faster() -> None:
    verdict = compare(result(1.0), result(0.2), correctness_passed=False)
    assert verdict.decision is Decision.REJECT


def test_marks_noisy_benchmark_inconclusive() -> None:
    verdict = compare(result(1.0, 0.3), result(0.7), correctness_passed=True)
    assert verdict.decision is Decision.INCONCLUSIVE




def test_tiny_cpu_measurements_do_not_trigger_regression_rejection() -> None:
    baseline = BenchmarkResult(
        ("work",), (1.0,) * 5, 1.0, 1.0, 0.001, 1.0, 1.0, cpu_mean_seconds=0.01
    )
    candidate = BenchmarkResult(
        ("work",), (0.7,) * 5, 0.7, 0.7, 0.001, 0.7, 0.7, cpu_mean_seconds=0.02
    )
    verdict = compare(baseline, candidate, correctness_passed=True)
    assert verdict.cpu_change_percent == 100.0
    assert verdict.decision is Decision.ACCEPT


def test_cpu_regression_is_enforced_with_measurable_cpu_time() -> None:
    baseline = BenchmarkResult(
        ("work",), (1.0,) * 5, 1.0, 1.0, 0.001, 1.0, 1.0, cpu_mean_seconds=0.10
    )
    candidate = BenchmarkResult(
        ("work",), (0.7,) * 5, 0.7, 0.7, 0.001, 0.7, 0.7, cpu_mean_seconds=0.12
    )
    verdict = compare(baseline, candidate, correctness_passed=True)
    assert verdict.cpu_change_percent == pytest.approx(20.0)
    assert verdict.decision is Decision.REJECT
    assert verdict.reason == "candidate exceeds the CPU regression budget"
