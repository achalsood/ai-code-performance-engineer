from perf_engineer.evaluation import CaseResult, CorpusCase, confidence_interval, summarize
from perf_engineer.models import BenchmarkResult, Decision, VerificationResult


def measured(value: float) -> BenchmarkResult:
    return BenchmarkResult(("work",), (value,) * 3, value, value, 0.0, value, value)


def case_result(speedup: float, decision: Decision = Decision.ACCEPT) -> CaseResult:
    baseline = measured(1.0)
    candidate = measured(1.0 - speedup / 100)
    verification = VerificationResult(
        decision, speedup, True, True, "measured", baseline, candidate
    )
    case = CorpusCase(
        "case", "description", "before", "after", ("work",), ("test",), category="algorithms", language="python"
    )
    return CaseResult(case, verification)


def test_confidence_interval_is_deterministic() -> None:
    assert confidence_interval([10.0, 20.0, 30.0]) == confidence_interval([10.0, 20.0, 30.0])


def test_summarizes_effectiveness() -> None:
    summary = summarize([case_result(20.0), case_result(10.0, Decision.REJECT)])
    assert summary.acceptance_rate == 50.0
    assert summary.correctness_rate == 100.0
    assert summary.median_speedup_percent == 15.0


def test_summarizes_categories() -> None:
    summary = summarize([case_result(20.0), case_result(10.0, Decision.REJECT)])
    assert len(summary.categories) == 1
    category = summary.categories[0]
    assert category.category == "algorithms"
    assert category.total_cases == 2
    assert category.acceptance_rate == 50.0
    assert category.median_speedup_percent == 15.0
