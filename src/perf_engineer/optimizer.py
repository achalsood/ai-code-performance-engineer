from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .analyzer import analyze_path
from .benchmark import run_benchmark
from .models import BenchmarkResult, Decision, VerificationResult
from .patches import PatchValidationError, apply_patch
from .providers import CandidateProvider, OptimizationCandidate, OptimizationRequest
from .repository import resolve_commit
from .verification import compare, run_correctness


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: OptimizationCandidate
    status: str
    result: VerificationResult | None
    error: str | None
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class OptimizationRun:
    baseline_commit: str
    baseline: BenchmarkResult
    evaluations: tuple[CandidateEvaluation, ...]
    winner_id: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _git(repository: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


@contextlib.contextmanager
def _worktree(repository: Path, commit: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="perf-candidate-") as directory:
        path = Path(directory) / "worktree"
        _git(repository, "worktree", "add", "--detach", str(path), commit)
        try:
            yield path
        finally:
            subprocess.run(
                ["git", "-C", str(repository), "worktree", "remove", "--force", str(path)],
                capture_output=True,
                check=False,
            )
            subprocess.run(
                ["git", "-C", str(repository), "worktree", "prune"],
                capture_output=True,
                check=False,
            )


def _request(repository: Path, worktree: Path, maximum_candidates: int) -> OptimizationRequest:
    findings = tuple(analyze_path(worktree))
    relevant_paths = sorted({Path(item.path) for item in findings})[:20]
    files: dict[str, str] = {}
    for absolute_path in relevant_paths:
        relative = absolute_path.relative_to(worktree)
        content = absolute_path.read_text(encoding="utf-8")
        files[str(relative)] = content[:30_000]
    return OptimizationRequest(
        objective="Improve runtime or memory use without changing observable behavior.",
        language="python",
        findings=findings,
        files=files,
        maximum_candidates=maximum_candidates,
    )


def optimize(
    *,
    repository: Path,
    baseline_ref: str,
    provider: CandidateProvider,
    benchmark_command: list[str],
    test_command: list[str],
    rounds: int = 7,
    maximum_candidates: int = 3,
    minimum_improvement_percent: float = 5.0,
) -> OptimizationRun:
    repository = repository.resolve()
    commit = resolve_commit(repository, baseline_ref)
    with _worktree(repository, commit) as baseline_tree:
        baseline = run_benchmark(benchmark_command, cwd=baseline_tree, rounds=rounds)
        request = _request(repository, baseline_tree, maximum_candidates)
        candidates = provider.generate(request)

    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        try:
            with _worktree(repository, commit) as candidate_tree:
                changed_paths = apply_patch(candidate_tree, candidate.patch)
                correctness = run_correctness(test_command, cwd=candidate_tree)
                measured = run_benchmark(benchmark_command, cwd=candidate_tree, rounds=rounds)
                result = compare(
                    baseline,
                    measured,
                    correctness_passed=correctness,
                    minimum_improvement_percent=minimum_improvement_percent,
                )
                evaluations.append(
                    CandidateEvaluation(
                        candidate, result.decision.value, result, None, changed_paths
                    )
                )
        except (PatchValidationError, OSError, RuntimeError, ValueError) as exc:
            evaluations.append(CandidateEvaluation(candidate, "invalid", None, str(exc), ()))

    accepted = [
        item
        for item in evaluations
        if item.result and item.result.decision is Decision.ACCEPT
    ]
    accepted.sort(
        key=lambda item: (
            -item.result.speedup_percent,
            item.result.candidate.peak_memory_bytes,
            item.candidate.candidate_id,
        )
        if item.result
        else (0.0, 0, "")
    )
    winner = accepted[0].candidate.candidate_id if accepted else None
    return OptimizationRun(commit, baseline, tuple(evaluations), winner)
