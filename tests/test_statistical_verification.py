from perf_engineer.models import BenchmarkResult, Decision
from perf_engineer.verification import compare, speedup_confidence_interval


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
