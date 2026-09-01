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

