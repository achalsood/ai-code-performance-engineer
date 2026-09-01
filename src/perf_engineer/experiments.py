from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .benchmark import run_benchmark
from .models import ExperimentRecord
from .repository import isolated_worktrees
from .verification import compare, run_correctness


def run_experiment(
    *,
    repository: Path,
    baseline_ref: str,
    candidate_ref: str,
    benchmark_command: list[str],
    test_command: list[str],
    rounds: int = 7,
    minimum_improvement_percent: float = 5.0,
) -> ExperimentRecord:
    with isolated_worktrees(repository, baseline_ref, candidate_ref) as worktrees:
        correctness = run_correctness(test_command, cwd=worktrees.candidate)
        baseline = run_benchmark(benchmark_command, cwd=worktrees.baseline, rounds=rounds)
        candidate = run_benchmark(benchmark_command, cwd=worktrees.candidate, rounds=rounds)
        result = compare(
            baseline,
            candidate,
            correctness_passed=correctness,
            minimum_improvement_percent=minimum_improvement_percent,
        )
        return ExperimentRecord(
            schema_version=1,
            experiment_id=str(uuid.uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            repository=str(repository.resolve()),
            baseline_ref=baseline_ref,
            baseline_commit=worktrees.baseline_commit,
            candidate_ref=candidate_ref,
            candidate_commit=worktrees.candidate_commit,
            benchmark_command=tuple(benchmark_command),
            test_command=tuple(test_command),
            result=result,
        )


def save_record(record: ExperimentRecord, output_directory: Path) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / f"{record.experiment_id}.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{record.experiment_id}-", suffix=".tmp", dir=output_directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(asdict(record), temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return destination
