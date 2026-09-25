from pathlib import Path

import pytest

from perf_engineer.agent_arena import _validate_agent_patch, run_agent_arena
from perf_engineer.patches import PatchValidationError
from perf_engineer.providers import OptimizationCandidate, OptimizationRequest


class EmptyProvider:
    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        return []


def test_agent_arena_records_empty_provider(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    baseline = root / "case" / "baseline"
    candidate = root / "case" / "candidate"
    baseline.mkdir(parents=True)
    candidate.mkdir(parents=True)
    for directory in (baseline, candidate):
        (directory / "solution.py").write_text("VALUE = 1\n")
        (directory / "workload.py").write_text("from solution import VALUE\nassert VALUE == 1\n")
        (directory / "test_correctness.py").write_text(
            "from solution import VALUE\nassert VALUE == 1\n"
        )
    corpus = root / "corpus.json"
    corpus.write_text(
        '{"suite_name":"test","cases":[{"case_id":"case","description":"test",'
        '"category":"algorithms","language":"python","baseline_directory":"case/baseline",'
        '"candidate_directory":"case/candidate","benchmark_command":["python","workload.py"],'
        '"test_command":["python","test_correctness.py"],"minimum_improvement_percent":5}]}'
    )
    run = run_agent_arena(
        corpus,
        provider=EmptyProvider(),
        provider_label="empty",
        rounds=1,
        maximum_rounds=1,
    )
    assert run.results[0].status == "provider-failure"


def test_agent_patch_cannot_modify_hidden_evaluation_files() -> None:
    patch = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1 +1 @@
-print("slow")
+print("fast")
"""
    with pytest.raises(PatchValidationError, match="cannot modify"):
        _validate_agent_patch(patch)


def test_agent_run_summary_counts_failures() -> None:
    from perf_engineer.agent_arena import AgentArenaRun, AgentCaseResult

    run = AgentArenaRun(
        1,
        "suite",
        "provider",
        "2026-09-25T00:00:00+00:00",
        (
            AgentCaseResult("a", "algorithms", "python", "accepted", "1", 20.0, 12.0, None),
            AgentCaseResult("b", "algorithms", "python", "provider-failure", None, None, None, "x"),
        ),
    )
    summary = run.summary()
    assert summary["total_cases"] == 2
    assert summary["acceptance_rate"] == 0.5
    assert summary["correctness_rate"] == 0.5
    assert summary["median_accepted_speedup_percent"] == 20.0
