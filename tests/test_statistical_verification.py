from perf_engineer.models import BenchmarkResult, Decision
from perf_engineer.verification import (
    compare,
    paired_speedup_confidence_interval,
    speedup_confidence_interval,
)


def measured(samples: tuple[float, ...]) -> BenchmarkResult:
    mean = sum(samples) / len(samples)
    return BenchmarkResult(
        ("work",),
        samples,
        sorted(samples)[len(samples) // 2],
        mean,
        0.0,
        min(samples),
        max(samples),
    )


def test_rejects_point_improvement_when_confidence_crosses_threshold() -> None:
    baseline = measured((1.0, 1.0, 2.0))
    candidate = measured((0.8, 0.9, 1.8))
    result = compare(baseline, candidate, correctness_passed=True, minimum_improvement_percent=5)
    assert result.speedup_percent > 5
    assert result.decision is Decision.INCONCLUSIVE


def test_confidence_interval_is_deterministic() -> None:
    baseline = measured((1.0, 1.1, 1.2))
    candidate = measured((0.5, 0.6, 0.7))
    assert speedup_confidence_interval(baseline, candidate) == speedup_confidence_interval(
        baseline, candidate
    )


def test_paired_confidence_preserves_shared_machine_drift() -> None:
    baseline = measured((1.0, 2.0, 4.0, 8.0, 16.0))
    candidate = measured((0.8, 1.6, 3.2, 6.4, 12.8))
    low, high = paired_speedup_confidence_interval(baseline, candidate)
    assert round(low) == round(high) == 20
    verdict = compare(baseline, candidate, correctness_passed=True, paired=True)
    assert verdict.decision is Decision.ACCEPT


def test_rejects_runtime_win_that_exceeds_memory_budget() -> None:
    baseline = measured((1.0,) * 7)
    candidate = measured((0.7,) * 7)
    baseline = BenchmarkResult(**{**baseline.__dict__, "peak_memory_bytes": 100})
    candidate = BenchmarkResult(**{**candidate.__dict__, "peak_memory_bytes": 120})
    verdict = compare(baseline, candidate, correctness_passed=True, paired=True)
    assert verdict.decision is Decision.REJECT
    assert "memory" in verdict.reason


def test_rejects_runtime_win_that_exceeds_cpu_budget() -> None:
    baseline = BenchmarkResult(**{**measured((1.0,) * 7).__dict__, "cpu_mean_seconds": 0.5})
    candidate = BenchmarkResult(**{**measured((0.7,) * 7).__dict__, "cpu_mean_seconds": 0.6})
    verdict = compare(baseline, candidate, correctness_passed=True, paired=True)
    assert verdict.decision is Decision.REJECT
    assert "CPU" in verdict.reason
