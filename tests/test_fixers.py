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


def test_deterministic_provider_indexes_batched_nested_lookup() -> None:
    source = """def match_records(records, queries):
    result = []
    for query in queries:
        for record in records:
            if record["id"] == query["id"]:
                result.append(record)
                break
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
                "Use dictionary indexing when semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    candidates = DeterministicFixProvider().generate(request)
    assert len(candidates) == 1
    patch = candidates[0].patch
    assert "_perf_lookup_0 = {}" in patch
    assert "_perf_lookup_0.setdefault(record['id'], record)" in patch
    assert "_perf_lookup_0.get(query['id'])" in patch


def test_deterministic_provider_preserves_first_duplicate_match() -> None:
    source = """def match_records(records, queries):
    result = []
    for query in queries:
        for record in records:
            if record["id"] == query["id"]:
                result.append(record)
                break
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
                "Use dictionary indexing when semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    patch = DeterministicFixProvider().generate(request)[0].patch
    assert "setdefault" in patch



def test_deterministic_provider_refuses_mutated_invariant_result() -> None:
    source = """def ranks(values, queries):
    result = []
    for query in queries:
        ordered = sorted(values)
        ordered.pop()
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

    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_invariant_source_passed_to_unknown_call() -> None:
    source = """def ranks(values, queries):
    result = []
    for query in queries:
        ordered = sorted(values)
        inspect_values(values)
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

    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_mutated_invariant_result_alias() -> None:
    source = """def ranks(values, queries):
    result = []
    for query in queries:
        ordered = sorted(values)
        alias = ordered
        alias.pop()
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

    assert DeterministicFixProvider().generate(request) == []

def test_deterministic_provider_refuses_nested_loop_without_break() -> None:
    source = """def match_records(records, queries):
    result = []
    for query in queries:
        for record in records:
            if record["id"] == query["id"]:
                result.append(record)
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
                "Use dictionary indexing when semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_records_passed_to_unknown_call() -> None:
    source = """def match_records(records, queries):
    result = []
    for query in queries:
        inspect_records(records)
        for record in records:
            if record["id"] == query["id"]:
                result.append(record)
                break
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF001",
                "example.py",
                5,
                "medium",
                "Nested loop may scale quadratically.",
                "Use dictionary indexing when semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_adds_counter_import_after_future_import() -> None:
    source = """from __future__ import annotations

def frequencies(items):
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
                6,
                "high",
                ".count() performs a linear scan inside a loop.",
                "Precompute a lookup dictionary or set.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    patch = DeterministicFixProvider().generate(request)[0].patch
    assert "from __future__ import annotations" in patch
    assert "from collections import Counter" in patch
    assert "Counter(items)" in patch


def test_deterministic_provider_reuses_existing_counter_import() -> None:
    source = """from collections import Counter

def frequencies(items):
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
                6,
                "high",
                ".count() performs a linear scan inside a loop.",
                "Precompute a lookup dictionary or set.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    patch = DeterministicFixProvider().generate(request)[0].patch
    assert "+from collections import Counter" not in patch
    assert "+    _perf_counts_0 = Counter(items)" in patch


def test_deterministic_provider_refuses_count_when_collection_mutates() -> None:
    source = """def frequencies(items, queries):
    result = []
    for query in queries:
        result.append(items.count(query))
        items.append(query)
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
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_count_when_collection_escapes() -> None:
    source = """def frequencies(items, queries):
    result = []
    for query in queries:
        inspect_items(items)
        result.append(items.count(query))
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF003",
                "example.py",
                5,
                "high",
                ".count() performs a linear scan inside a loop.",
                "Precompute a lookup dictionary or set.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_indexes_repeated_membership() -> None:
    source = """def present():
    values = [1, 2, 3, 4, 5]
    result = []
    for query in range(10):
        result.append(query in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                4,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    candidates = DeterministicFixProvider().generate(request)
    assert len(candidates) == 1
    patch = candidates[0].patch
    assert "_perf_membership_0 = set(values)" in patch
    assert "query in _perf_membership_0" in patch


def test_deterministic_provider_refuses_membership_when_collection_mutates() -> None:
    source = """def present(values, queries):
    result = []
    for query in queries:
        result.append(query in values)
        values.append(query)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                4,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_membership_when_collection_escapes() -> None:
    source = """def present(values, queries):
    result = []
    for query in queries:
        inspect_values(values)
        result.append(query in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                5,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_computed_membership_probe() -> None:
    source = """def present(values, queries):
    result = []
    for query in queries:
        result.append(query["id"] in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                4,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_membership_for_unknown_collection_elements() -> None:
    source = """def present(values, queries):
    result = []
    for query in queries:
        result.append(query in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                4,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_indexes_known_hash_safe_range() -> None:
    source = """def present():
    values = list(range(1000))
    result = []
    for query in range(2000):
        result.append(query in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                5,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    patch = DeterministicFixProvider().generate(request)[0].patch
    assert "_perf_membership_0 = set(values)" in patch


def test_deterministic_provider_avoids_membership_index_name_collision() -> None:
    source = """def present():
    values = [1, 2, 3, 4, 5]
    _perf_membership_0 = "preserve-me"
    result = []
    for query in range(10):
        result.append((query in values, _perf_membership_0))
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                5,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    candidates = DeterministicFixProvider().generate(request)
    assert len(candidates) == 1
    patch = candidates[0].patch
    assert "_perf_membership_1 = set(values)" in patch
    assert "query in _perf_membership_1" in patch
    assert "_perf_membership_0 = 'preserve-me'" in patch


def test_deterministic_provider_refuses_membership_when_alias_mutates() -> None:
    source = """def present(queries):
    values = [1, 2, 3, 4, 5]
    result = []
    for query in queries:
        alias = values
        result.append(query in values)
        alias.append(query)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                6,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_membership_when_alias_escapes() -> None:
    source = """def present(queries):
    values = [1, 2, 3, 4, 5]
    result = []
    for query in queries:
        alias = values
        inspect_values(alias)
        result.append(query in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                7,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_refuses_membership_for_unknown_probe_elements() -> None:
    source = """def present(queries):
    values = [1, 2, 3, 4, 5]
    result = []
    for query in queries:
        result.append(query in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                5,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    assert DeterministicFixProvider().generate(request) == []


def test_deterministic_provider_indexes_membership_for_hash_safe_probe_range() -> None:
    source = """def present():
    values = list(range(1000))
    result = []
    for query in range(2000):
        result.append(query not in values)
    return result
"""
    request = OptimizationRequest(
        objective="optimize",
        language="python",
        findings=(
            Finding(
                "PERF004",
                "example.py",
                5,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            ),
        ),
        files={"example.py": source},
        maximum_candidates=3,
    )
    patch = DeterministicFixProvider().generate(request)[0].patch
    assert "_perf_membership_0 = set(values)" in patch
    assert "query not in _perf_membership_0" in patch
