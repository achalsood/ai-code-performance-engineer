import json
from pathlib import Path

from perf_engineer.experiments import save_record
from perf_engineer.models import BenchmarkResult, Decision, ExperimentRecord, VerificationResult


def benchmark() -> BenchmarkResult:
    return BenchmarkResult(("python",), (1.0,) * 3, 1.0, 1.0, 0.0, 1.0, 1.0)


def test_saves_versioned_record_atomically(tmp_path: Path) -> None:
    measured = benchmark()
    verification = VerificationResult(
        Decision.ACCEPT, 10.0, True, True, "faster", measured, measured
    )
    record = ExperimentRecord(
        1, "experiment-id", "2026-01-01T00:00:00+00:00", "/repo", "main~1", "a" * 40,
        "main", "b" * 40, ("python", "bench.py"), ("pytest",), verification
    )
    destination = save_record(record, tmp_path / "records")
    payload = json.loads(destination.read_text())
    assert payload["schema_version"] == 1
    assert payload["result"]["decision"] == "accept"
    assert not list(destination.parent.glob("*.tmp"))


def test_run_experiment_compares_isolated_revisions(tmp_path: Path, monkeypatch) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from perf_engineer.experiments import run_experiment
    from perf_engineer.models import Decision, VerificationResult

    baseline = benchmark()
    candidate = benchmark()

    @contextmanager
    def fake_worktrees(repository, baseline_ref, candidate_ref):
        yield SimpleNamespace(
            baseline=tmp_path / "baseline",
            candidate=tmp_path / "candidate",
            baseline_commit="a" * 40,
            candidate_commit="b" * 40,
        )

    monkeypatch.setattr("perf_engineer.experiments.isolated_worktrees", fake_worktrees)
    monkeypatch.setattr("perf_engineer.experiments.run_correctness", lambda *args, **kwargs: True)
    measurements = iter((baseline, candidate))
    monkeypatch.setattr(
        "perf_engineer.experiments.run_benchmark",
        lambda *args, **kwargs: next(measurements),
    )
    verification = VerificationResult(
        Decision.ACCEPT, 10.0, True, True, "faster", baseline, candidate
    )
    monkeypatch.setattr("perf_engineer.experiments.compare", lambda *args, **kwargs: verification)
    monkeypatch.setattr(
        "perf_engineer.experiments.environment_fingerprint",
        lambda: {"platform": "test"},
    )

    record = run_experiment(
        repository=tmp_path,
        baseline_ref="main~1",
        candidate_ref="main",
        benchmark_command=["python", "bench.py"],
        test_command=["pytest"],
        rounds=3,
        minimum_improvement_percent=5.0,
    )

    assert record.baseline_commit == "a" * 40
    assert record.candidate_commit == "b" * 40
    assert record.result is verification
    assert record.environment == {"platform": "test"}
