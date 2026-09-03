from __future__ import annotations

import json
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .benchmark import run_paired_benchmarks
from .environment import environment_fingerprint
from .execution import CommandRunner, ExecutionPolicy, LocalProcessRunner
from .models import Decision, VerificationResult
from .verification import compare, run_correctness


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    description: str
    baseline_directory: str
    candidate_directory: str
    benchmark_command: tuple[str, ...]
    test_command: tuple[str, ...]
    minimum_improvement_percent: float = 5.0


@dataclass(frozen=True)
class CaseResult:
    case: CorpusCase
    verification: VerificationResult


@dataclass(frozen=True)
class EvaluationSummary:
    total_cases: int
    accepted_cases: int
    correctness_rate: float
    acceptance_rate: float
    median_speedup_percent: float
    speedup_ci95_low: float
    speedup_ci95_high: float


@dataclass(frozen=True)
class EvaluationRun:
    schema_version: int
    suite_name: str
    created_at: str
    results: tuple[CaseResult, ...]
    summary: EvaluationSummary
    environment: dict[str, str | int | None] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def confidence_interval(
    values: list[float], *, confidence: float = 0.95, resamples: int = 2000, seed: int = 42
) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    generator = random.Random(seed)
    estimates = sorted(
        statistics.median(generator.choices(values, k=len(values))) for _ in range(resamples)
    )
    tail = (1.0 - confidence) / 2.0
    low = estimates[int(tail * (resamples - 1))]
    high = estimates[int((1.0 - tail) * (resamples - 1))]
    return low, high


def summarize(results: list[CaseResult]) -> EvaluationSummary:
    total = len(results)
    speedups = [item.verification.speedup_percent for item in results]
    accepted = sum(item.verification.decision is Decision.ACCEPT for item in results)
    correct = sum(item.verification.correctness_passed for item in results)
    low, high = confidence_interval(speedups)
    return EvaluationSummary(
        total_cases=total,
        accepted_cases=accepted,
        correctness_rate=correct / total * 100 if total else 0.0,
        acceptance_rate=accepted / total * 100 if total else 0.0,
        median_speedup_percent=statistics.median(speedups) if speedups else 0.0,
        speedup_ci95_low=low,
        speedup_ci95_high=high,
    )


def load_corpus(path: Path) -> tuple[str, list[CorpusCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        CorpusCase(
            **{
                **item,
                "benchmark_command": tuple(item["benchmark_command"]),
                "test_command": tuple(item["test_command"]),
            }
        )
        for item in payload["cases"]
    ]
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("corpus must contain uniquely identified cases")
    return payload["suite_name"], cases


def evaluate_corpus(
    corpus_path: Path,
    *,
    rounds: int = 7,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
) -> EvaluationRun:
    suite_name, cases = load_corpus(corpus_path)
    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy()
    root = corpus_path.parent
    results: list[CaseResult] = []
    for case in cases:
        baseline_directory = (root / case.baseline_directory).resolve()
        candidate_directory = (root / case.candidate_directory).resolve()
        correctness = run_correctness(
            list(case.test_command),
            cwd=candidate_directory,
            runner=selected_runner,
            policy=selected_policy,
        )
        baseline, candidate = run_paired_benchmarks(
            list(case.benchmark_command),
            baseline_cwd=baseline_directory,
            candidate_cwd=candidate_directory,
            rounds=rounds,
            runner=selected_runner,
            policy=selected_policy,
        )
        verification = compare(
            baseline,
            candidate,
            correctness_passed=correctness,
            minimum_improvement_percent=case.minimum_improvement_percent,
        )
        results.append(CaseResult(case, verification))
    return EvaluationRun(
        schema_version=1,
        suite_name=suite_name,
        created_at=datetime.now(UTC).isoformat(),
        results=tuple(results),
        summary=summarize(results),
        environment=environment_fingerprint(),
    )
