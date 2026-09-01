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
