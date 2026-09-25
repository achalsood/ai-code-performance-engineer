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


def test_deterministic_provider_hoists_invariant_sorted_call() -> None:
    source = """def ranks(values, queries):
    result = []
    for query in queries:
        ordered = sorted(values)
        result.append((query, ordered[0]))
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF002",
                "example.py",
                4,
                "medium",
                "sorted() allocates inside a loop.",
                "Hoist invariant allocation outside the loop when semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    candidates = DeterministicFixProvider().generate(request)
    assert len(candidates) == 1
    patch = candidates[0].patch
    assert "_perf_invariant_0 = sorted(values)" in patch
    assert "ordered = _perf_invariant_0" in patch


def test_deterministic_provider_does_not_hoist_mutated_input() -> None:
    source = """def snapshots(values, additions):
    result = []
    for addition in additions:
        values.append(addition)
        result.append(sorted(values))
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF002",
                "example.py",
                5,
                "medium",
                "sorted() allocates inside a loop.",
                "Hoist invariant allocation outside the loop when semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_indexes_nested_equality_lookup() -> None:
    source = """def find_record(records, queries):
    for query in queries:
        for record in records:
            if record["id"] == query["id"]:
                return record
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF001",
                "example.py",
                3,
                "medium",
                "Nested loop may scale quadratically.",
                "Consider indexing lookup data in a set or dictionary.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    candidates = DeterministicFixProvider().generate(request)
    assert len(candidates) == 1
    patch = candidates[0].patch
    assert "_perf_lookup_0" in patch
    assert "_perf_lookup_0.get(query['id'])" in patch


def test_deterministic_provider_refuses_complex_nested_loop() -> None:
    source = """def pairs(left, right):
    result = []
    for first in left:
        for second in right:
            result.append((first, second))
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF001",
                "example.py",
                4,
                "medium",
                "Nested loop may scale quadratically.",
                "Consider indexing lookup data in a set or dictionary.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []
