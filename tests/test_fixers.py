from perf_engineer.fixers import DeterministicFixProvider
from perf_engineer.models import Finding
from perf_engineer.providers import OptimizationRequest


def test_deterministic_provider_rewrites_count_in_loop() -> None:
    source = """def frequencies(items):
    result = []
    for item in items:
        result.append(items.count(item))
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF003",
                "example.py",
                4,
                "high",
                ".count() performs a linear scan inside a loop.",
                "Precompute a lookup dictionary or set.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    candidates = DeterministicFixProvider().generate(request)
    assert len(candidates) == 1
    patch = candidates[0].patch
    assert "_perf_counts_0" in patch
    assert "items.count(item)" in patch
