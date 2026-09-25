import subprocess
import sys
from pathlib import Path

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
        rounds=3,
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
    (repository / "workload.py").write_text("import time\ntime.sleep(0.10)\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    provider = RefiningProvider()

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=provider,
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        rounds=3,
        maximum_rounds=3,
        profile_guidance=False,
    )

    assert result.winner_id == "fast"
    assert result.provider_attempts == 2
    assert provider.requests[1].attempt_number == 2
    assert "invalid" in provider.requests[1].feedback[0]


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

    assert result.winner_id == "deterministic-1"
    evaluation = result.evaluations[0]
    assert evaluation.status == "accept"
    assert evaluation.result is not None
    assert evaluation.result.correctness_passed
    assert evaluation.result.speedup_percent >= 5.0
    assert evaluation.result.speedup_ci95_low >= 5.0
    assert evaluation.changed_paths == ("workload.py",)


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

values = list(range(6000, 0, -1))
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


def test_nested_lookup_closes_analyze_fix_verify_loop(tmp_path: Path) -> None:
    from perf_engineer.fixers import DeterministicFixProvider

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "workload.py").write_text(
        """def find_record(records, queries):
    for query in queries:
        for record in records:
            if record["id"] == query["id"]:
                return record
    return None

records = [{"id": value, "payload": value * 2} for value in range(12000)]
queries = [{"id": 11999}]
for _ in range(120):
    find_record(records, queries)
"""
    )
    (repository / "test_correctness.py").write_text(
        """from workload import find_record

records = [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}]
assert find_record(records, [{"id": 2}]) == {"id": 2, "name": "two"}
assert find_record(records, [{"id": 99}]) is None
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
