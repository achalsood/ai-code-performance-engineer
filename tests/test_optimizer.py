import subprocess
import sys
from pathlib import Path

import pytest

from perf_engineer.callable_benchmark import PythonCallableTarget
from perf_engineer.optimizer import export_winning_patch, optimize, save_optimization
from perf_engineer.providers import OptimizationCandidate, OptimizationRequest


class FixedProvider:
    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        assert request.language == "python"
        patch = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,2 +1,2 @@
 import time
-time.sleep(0.10)
+time.sleep(0.01)
"""
        return [OptimizationCandidate("fast", "Reduce wait", "Removes idle time", patch)]


class RefiningProvider:
    def __init__(self) -> None:
        self.requests: list[OptimizationRequest] = []

    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        self.requests.append(request)
        if request.attempt_number == 1:
            return [OptimizationCandidate("bad", "Invalid", "First attempt", "not a diff")]
        patch = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,2 +1,2 @@
 import time
-time.sleep(0.10)
+time.sleep(0.01)
"""
        return [
            OptimizationCandidate(
                "fast", "Reduce wait", "Uses feedback", patch, "repeated-work", "90%", "low"
            )
        ]


def test_ranks_verified_candidate_and_cleans_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text("import time\ntime.sleep(0.10)\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=FixedProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        rounds=5,
        maximum_rounds=9,
    )

    assert result.winner_id == "fast"
    assert result.evaluations[0].status == "accept"
    assert result.baseline_profile is not None
    record = save_optimization(result, tmp_path / "records")
    patch = export_winning_patch(result, tmp_path / "winner.patch")
    assert record.exists()
    assert patch and "time.sleep(0.01)" in patch.read_text()
    worktrees = subprocess.check_output(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"], text=True
    )
    assert worktrees.count("worktree ") == 1


def test_refines_failed_ai_candidates_with_measurement_feedback(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text("import time\ntime.sleep(0.20)\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    provider = RefiningProvider()

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=provider,
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        rounds=5,
        maximum_rounds=7,
        profile_guidance=False,
    )

    assert result.provider_attempts == 2
    assert provider.requests[1].attempt_number == 2
    assert any(evaluation.candidate.candidate_id == "fast" for evaluation in result.evaluations)


def test_optimizer_benchmarks_next_candidate_on_accepted_state(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        "import time\ntime.sleep(0.20)\nvalue = 1\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    class CumulativeProvider:
        def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
            first = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,3 +1,3 @@
 import time
-time.sleep(0.20)
+time.sleep(0.10)
 value = 1
"""
            second = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,3 +1,3 @@
 import time
-time.sleep(0.10)
+time.sleep(0.01)
 value = 1
"""
            return [
                OptimizationCandidate("first", "First", "First speedup", first),
                OptimizationCandidate("second", "Second", "Builds on first", second),
            ]

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=CumulativeProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        rounds=5,
        maximum_rounds=7,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    assert [item.status for item in result.evaluations] == ["accept", "accept"]
    second_result = result.evaluations[1].result
    assert second_result is not None
    assert second_result.baseline.median_seconds < 0.18
    assert result.winner_id == "second"


def test_applies_compatible_candidates_as_cumulative_state(tmp_path: Path) -> None:
    import perf_engineer.optimizer as optimizer

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "workload.py").write_text("value = 1\nother = 2\n", encoding="utf-8")
    first = OptimizationCandidate(
        "first",
        "Change value",
        "First independent optimization",
        """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,2 +1,2 @@
-value = 1
+value = 10
 other = 2
""",
    )
    second = OptimizationCandidate(
        "second",
        "Change other",
        "Second independent optimization",
        """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,2 +1,2 @@
 value = 10
-other = 2
+other = 20
""",
    )

    changed = optimizer._apply_candidate_sequence(repository, (first, second))

    assert changed == ("workload.py",)
    assert (repository / "workload.py").read_text(encoding="utf-8") == (
        "value = 10\nother = 20\n"
    )


def test_cumulative_speedup_is_measured_from_original_baseline() -> None:
    import perf_engineer.optimizer as optimizer

    assert optimizer._cumulative_speedup_percent(1.0, 0.8) == pytest.approx(20.0)
    assert optimizer._cumulative_speedup_percent(1.0, 0.6) == pytest.approx(40.0)
    assert optimizer._cumulative_speedup_percent(0.0, 0.6) == 0.0


def test_refinement_feedback_contains_structured_attribution() -> None:
    import perf_engineer.optimizer as optimizer
    from perf_engineer.models import (
        BenchmarkResult,
        Decision,
        PerformanceAttribution,
        VerificationResult,
    )

    baseline = BenchmarkResult(("bench",), (1.0,), 1.0, 1.0, 0.0, 1.0, 1.0)
    measured = BenchmarkResult(("bench",), (0.8,), 0.8, 0.8, 0.0, 0.8, 0.8)
    result = VerificationResult(
        Decision.REJECT,
        20.0,
        True,
        True,
        "memory regression",
        baseline,
        measured,
        speedup_ci95_low=15.0,
        speedup_ci95_high=25.0,
        memory_change_percent=12.0,
        cpu_change_percent=-18.0,
    )
    candidate = OptimizationCandidate(
        "candidate-1",
        "Index membership",
        "Avoid repeated scans",
        "patch",
        "membership-index",
        target_evidence_ids=("finding:PERF001:workload.py:7",),
    )
    attribution = PerformanceAttribution(
        "PERF001 at workload.py:7: Repeated linear membership scan",
        "membership-index",
        1.0,
        0.8,
        -20.0,
        1.0,
        0.8,
        -18.0,
        100,
        112,
        12.0,
        True,
        True,
        "high",
        Decision.REJECT,
    )
    evaluation = optimizer.CandidateEvaluation(
        candidate,
        "reject",
        result,
        None,
        ("workload.py",),
        attribution=attribution,
    )

    feedback = optimizer._candidate_feedback([evaluation])[0]

    assert "target=PERF001 at workload.py:7" in feedback
    assert "evidence=finding:PERF001:workload.py:7" in feedback
    assert "confidence=high" in feedback
    assert "wall_change=-20.00%" in feedback
    assert "memory_change=12.00%" in feedback

    hints = optimizer._refinement_hints([evaluation])

    assert len(hints) == 2
    assert "Avoid repeating strategy membership-index" in hints[0]
    assert "preserve the wall-time improvement" in hints[1]


def test_attribution_resolves_candidate_evidence_to_analyzer_finding(tmp_path: Path) -> None:
    import perf_engineer.optimizer as optimizer
    from perf_engineer.models import BenchmarkResult, Decision, Finding, VerificationResult

    candidate = OptimizationCandidate(
        "indexed",
        "Index membership",
        "Avoid repeated scans",
        "patch",
        "membership-index",
        target_evidence_ids=("finding:PERF001:workload.py:7",),
    )
    request = OptimizationRequest(
        objective="Improve runtime",
        language="python",
        findings=(
            Finding(
                "PERF001",
                "workload.py",
                7,
                "high",
                "Repeated linear membership scan",
                "Build an index once",
            ),
        ),
        files={},
        maximum_candidates=1,
    )
    benchmark = BenchmarkResult(("bench",), (1.0,), 1.0, 1.0, 0.0, 1.0, 1.0)
    result = VerificationResult(
        Decision.ACCEPT,
        10.0,
        True,
        True,
        "verified",
        benchmark,
        benchmark,
    )

    attribution = optimizer._attribution(candidate, result, request)

    assert attribution.targeted_issue == (
        "PERF001 at workload.py:7: Repeated linear membership scan"
    )


def test_optimizer_selects_verified_callable_speedup(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        "def benchmark():\n"
        "    total = 0\n"
        "    for i in range(20000):\n"
        "        total += i * i\n"
        "    return total\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    class CallableProvider:
        def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
            patch = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,5 +1,2 @@
 def benchmark():
-    total = 0
-    for i in range(20000):
-        total += i * i
-    return total
+    return sum(i * i for i in range(2000))
"""
            return [OptimizationCandidate("callable-fast", "Faster callable", "Less work", patch)]

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=CallableProvider(),
        benchmark_command=None,
        benchmark_callable=PythonCallableTarget("workload", "benchmark"),
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        rounds=3,
        maximum_rounds=5,
        profile_guidance=False,
    )

    assert result.winner_id == "callable-fast"
    assert result.evaluations[0].status == "accept"
    assert result.evaluations[0].result is not None
    assert result.evaluations[0].result.speedup_percent > 5.0
    assert result.baseline.repetitions_per_sample > 1
    assert result.evaluations[0].result.candidate.peak_memory_bytes > 0
    attribution = result.evaluations[0].attribution
    assert attribution is not None
    assert attribution.targeted_issue == "Less work"
    assert attribution.strategy == "unspecified"
    assert attribution.correctness_passed
    assert attribution.decision.value == "accept"
    assert attribution.baseline_wall_seconds > attribution.candidate_wall_seconds
    assert attribution.wall_change_percent < 0
    assert attribution.confidence in {"medium", "high"}


@pytest.mark.performance
def test_deterministic_fixer_closes_analyze_fix_verify_loop(tmp_path: Path) -> None:
    from perf_engineer.fixers import DeterministicFixProvider

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def frequencies(items):
    result = []
    for item in items:
        result.append(items.count(item))
    return result

items = list(range(2000)) * 2
for _ in range(8):
    frequencies(items)
"""
    )
    (repository / "test_correctness.py").write_text(
        """from workload import frequencies

assert frequencies([3, 1, 3, 2, 3]) == [3, 1, 3, 1, 3]
assert frequencies([]) == []
"""
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=DeterministicFixProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "test_correctness.py"],
        rounds=5,
        maximum_rounds=9,
        minimum_improvement_percent=5.0,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    evaluation = result.evaluations[0]
    assert result.winner_id == "deterministic-1", (
        f"status={evaluation.status}; "
        f"reason={evaluation.result.reason if evaluation.result else 'no verification result'}; "
        f"speedup={evaluation.result.speedup_percent if evaluation.result else 'n/a'}; "
        f"ci95=[{evaluation.result.speedup_ci95_low if evaluation.result else 'n/a'}, "
        f"{evaluation.result.speedup_ci95_high if evaluation.result else 'n/a'}]; "
        f"memory_change={evaluation.result.memory_change_percent if evaluation.result else 'n/a'}; "
        f"cpu_change={evaluation.result.cpu_change_percent if evaluation.result else 'n/a'}"
    )
    assert evaluation.status == "accept"
    assert evaluation.result is not None
    assert evaluation.result.correctness_passed
    assert evaluation.result.speedup_percent >= 5.0
    assert evaluation.result.speedup_ci95_low >= 5.0
    assert evaluation.changed_paths == ("workload.py",)


@pytest.mark.performance
def test_invariant_hoist_closes_analyze_fix_verify_loop(tmp_path: Path) -> None:
    from perf_engineer.fixers import DeterministicFixProvider

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def rank_queries(values, queries):
    result = []
    for query in queries:
        ordered = sorted(values)
        result.append((query, ordered[0], ordered[-1]))
    return result

values = list(range(12000, 0, -1))
queries = list(range(250))
rank_queries(values, queries)
"""
    )
    (repository / "test_correctness.py").write_text(
        """from workload import rank_queries

assert rank_queries([3, 1, 2], [10, 20]) == [(10, 1, 3), (20, 1, 3)]
assert rank_queries([5], [1]) == [(1, 5, 5)]
"""
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=DeterministicFixProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "test_correctness.py"],
        rounds=5,
        maximum_rounds=9,
        minimum_improvement_percent=5.0,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    assert result.winner_id == "deterministic-1"
    evaluation = result.evaluations[0]
    assert evaluation.status == "accept"
    assert evaluation.result is not None
    assert evaluation.result.correctness_passed
    assert evaluation.result.speedup_percent >= 5.0
    assert evaluation.result.speedup_ci95_low >= 5.0
    assert evaluation.changed_paths == ("workload.py",)


@pytest.mark.performance
def test_batched_lookup_closes_analyze_fix_verify_loop(tmp_path: Path) -> None:
    from perf_engineer.fixers import DeterministicFixProvider

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def match_records(records, queries):
    result = []
    for query in queries:
        for record in records:
            if record["id"] == query["id"]:
                result.append(record)
                break
    return result

records = [{"id": i, "value": i * 2} for i in range(6000)]
queries = [{"id": i} for i in range(5000, 6000)]
for _ in range(3):
    match_records(records, queries)
"""
    )
    (repository / "test_correctness.py").write_text(
        """from workload import match_records

records = [
    {"id": 1, "value": "first"},
    {"id": 1, "value": "second"},
    {"id": 2, "value": "two"},
]
assert match_records(records, [{"id": 1}, {"id": 9}, {"id": 2}]) == [
    records[0],
    records[2],
]
assert match_records([], [{"id": 1}]) == []
assert match_records(records, []) == []
"""
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=DeterministicFixProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "test_correctness.py"],
        rounds=5,
        maximum_rounds=9,
        minimum_improvement_percent=5.0,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    assert result.winner_id == "deterministic-1"
    evaluation = result.evaluations[0]
    assert evaluation.status == "accept"
    assert evaluation.result is not None
    assert evaluation.result.correctness_passed
    assert evaluation.result.speedup_percent >= 5.0
    assert evaluation.result.speedup_ci95_low >= 5.0
    assert evaluation.changed_paths == ("workload.py",)


@pytest.mark.performance
def test_membership_index_closes_analyze_fix_verify_loop(tmp_path: Path) -> None:
    from perf_engineer.fixers import DeterministicFixProvider

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def present():
    values = list(range(12000))
    result = []
    for query in range(6000, 18000):
        result.append(query in values)
    return result

for _ in range(3):
    present()
"""
    )
    (repository / "test_correctness.py").write_text(
        """from workload import present

result = present()
assert result[0] is True
assert result[5999] is True
assert result[6000] is False
assert result[-1] is False
assert len(result) == 12000
"""
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=DeterministicFixProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "test_correctness.py"],
        rounds=5,
        maximum_rounds=9,
        minimum_improvement_percent=5.0,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    assert result.winner_id == "deterministic-1"
    evaluation = result.evaluations[0]
    assert evaluation.status == "accept"
    assert evaluation.result is not None
    assert evaluation.result.correctness_passed
    assert evaluation.result.speedup_percent >= 5.0
    assert evaluation.result.speedup_ci95_low >= 5.0
    assert evaluation.changed_paths == ("workload.py",)


@pytest.mark.performance
def test_plan_search_closes_analyze_fix_verify_loop(tmp_path: Path) -> None:
    from perf_engineer.fixers import DeterministicFixProvider

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def optimize_both():
    values = list(range(12000))
    membership = []
    for query in range(6000, 18000):
        membership.append(query in values)

    ordered_source = list(range(6000, 0, -1))
    ranked = []
    for query in range(250):
        ordered = sorted(ordered_source)
        ranked.append((query, ordered[0], ordered[-1]))
    return membership, ranked

for _ in range(2):
    optimize_both()
"""
    )
    (repository / "test_correctness.py").write_text(
        """from workload import optimize_both

membership, ranked = optimize_both()
assert membership[0] is True
assert membership[5999] is True
assert membership[6000] is False
assert membership[-1] is False
assert len(membership) == 12000
assert ranked[0] == (0, 1, 6000)
assert ranked[-1] == (249, 1, 6000)
assert len(ranked) == 250
"""
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=DeterministicFixProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "test_correctness.py"],
        rounds=5,
        maximum_rounds=9,
        minimum_improvement_percent=5.0,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    assert [evaluation.candidate.strategy for evaluation in result.evaluations] == [
        "membership-index",
        "hoist-invariant-work",
        "combined:membership-index+hoist-invariant-work",
    ]
    combined = result.evaluations[2]
    assert "_perf_membership_0" in combined.candidate.patch
    assert "_perf_invariant_0" in combined.candidate.patch
    assert combined.status == "accept"
    assert combined.result is not None
    assert combined.result.correctness_passed
    assert combined.result.speedup_percent >= 5.0
    assert combined.result.speedup_ci95_low >= 5.0
    assert combined.changed_paths == ("workload.py",)

    accepted_ids = {
        evaluation.candidate.candidate_id
        for evaluation in result.evaluations
        if evaluation.status == "accept"
    }
    assert result.winner_id in accepted_ids



def test_optimizer_receives_evidence_ranked_deterministic_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import perf_engineer.optimizer as optimizer
    from perf_engineer.fixers import DeterministicFixProvider
    from perf_engineer.models import BenchmarkResult, Decision, Finding, VerificationResult

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def optimize_both():
    values = list(range(1000))
    present = []
    for query in range(2000):
        present.append(query in values)

    ordered_source = list(range(1000, 0, -1))
    ranked = []
    for query in range(20):
        ordered = sorted(ordered_source)
        ranked.append((query, ordered[0]))
    return present, ranked
"""
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    def fake_analyze(path: Path) -> list[Finding]:
        workload = path / "workload.py"
        return [
            Finding(
                "PERF004", str(workload), 5, "low",
                "Linear membership lookup executes inside a loop.", "Precompute a set.",
            ),
            Finding(
                "PERF002", str(workload), 11, "high",
                "Invariant sorting executes inside a loop.", "Hoist invariant sorting.",
            ),
        ]

    baseline = BenchmarkResult(
        command=("python", "workload.py"),
        samples_seconds=(1.0,),
        median_seconds=1.0,
        mean_seconds=1.0,
        stdev_seconds=0.0,
        min_seconds=1.0,
        max_seconds=1.0,
        cpu_mean_seconds=1.0,
        peak_memory_bytes=1024 * 1024,
        measurement_rounds=1,
    )

    def fake_pair(*args, **kwargs):
        result = VerificationResult(
            decision=Decision.ACCEPT,
            speedup_percent=10.0,
            correctness_passed=True,
            stable=True,
            reason="verified",
            baseline=baseline,
            candidate=baseline,
            speedup_ci95_low=10.0,
            speedup_ci95_high=10.0,
            memory_change_percent=0.0,
            cpu_change_percent=0.0,
        )
        return baseline, baseline, result

    monkeypatch.setattr(optimizer, "analyze_path", fake_analyze)
    monkeypatch.setattr(optimizer, "run_benchmark", lambda *args, **kwargs: baseline)
    monkeypatch.setattr(optimizer, "run_adaptive_paired_benchmarks", fake_pair)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=DeterministicFixProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        maximum_candidates=2,
        profile_guidance=False,
        maximum_provider_attempts=1,
    )

    assert [evaluation.candidate.strategy for evaluation in result.evaluations] == [
        "hoist-invariant-work",
        "membership-index",
    ]
