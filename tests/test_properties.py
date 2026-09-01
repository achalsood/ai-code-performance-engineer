from hypothesis import given
from hypothesis import strategies as st

from perf_engineer.models import BenchmarkResult, Decision
from perf_engineer.verification import compare


def measured(value: float) -> BenchmarkResult:
    return BenchmarkResult(("work",), (value,) * 3, value, value, 0.0, value, value)


@given(
    baseline=st.floats(min_value=0.001, max_value=1000, allow_nan=False),
    candidate=st.floats(min_value=0.001, max_value=1000, allow_nan=False),
)
def test_incorrect_candidate_is_never_accepted(baseline: float, candidate: float) -> None:
    result = compare(measured(baseline), measured(candidate), correctness_passed=False)
    assert result.decision is Decision.REJECT


@given(st.floats(min_value=0.001, max_value=1000, allow_nan=False))
def test_identical_performance_is_never_accepted(value: float) -> None:
    result = compare(measured(value), measured(value), correctness_passed=True)
    assert result.decision is Decision.REJECT
