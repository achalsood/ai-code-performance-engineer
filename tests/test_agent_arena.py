from pathlib import Path

from perf_engineer.agent_arena import run_agent_arena
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
        (directory / "test_correctness.py").write_text("from solution import VALUE\nassert VALUE == 1\n")
    corpus = root / "corpus.json"
    corpus.write_text(
        '{"suite_name":"test","cases":[{"case_id":"case","description":"test",'
        '"category":"algorithms","language":"python","baseline_directory":"case/baseline",'
        '"candidate_directory":"case/candidate","benchmark_command":["python","workload.py"],'
        '"test_command":["python","test_correctness.py"],"minimum_improvement_percent":5}]}'
    )
    run = run_agent_arena(corpus, provider=EmptyProvider(), provider_label="empty", rounds=1, maximum_rounds=1)
    assert run.results[0].status == "provider-failure"
