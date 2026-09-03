from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .analyzer import analyze_path
from .audit import AuditLogger
from .benchmark import run_adaptive_paired_benchmarks, run_benchmark
from .environment import environment_fingerprint
from .execution import CommandRunner, ExecutionPolicy, LocalProcessRunner
from .models import BenchmarkResult, Decision, VerificationResult
from .patches import PatchValidationError, apply_patch
from .providers import CandidateProvider, OptimizationCandidate, OptimizationRequest
from .redaction import redact_secrets
from .repository import resolve_commit
from .verification import compare, run_correctness


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: OptimizationCandidate
    status: str
    result: VerificationResult | None
    error: str | None
    changed_paths: tuple[str, ...]
    utility_score: float = 0.0


@dataclass(frozen=True)
class OptimizationRun:
    schema_version: int
    run_id: str
    created_at: str
    baseline_commit: str
    baseline: BenchmarkResult
    evaluations: tuple[CandidateEvaluation, ...]
    winner_id: str | None
    environment: dict[str, str | int | None] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _git(repository: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    raw_findings = tuple(analyze_path(worktree))
    finding_paths = {Path(item.path) for item in raw_findings}
    supported_suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
    fallback_paths = (
        path
        for path in sorted(worktree.rglob("*"))
        if path.suffix in supported_suffixes
        and not {".git", "node_modules", "__pycache__"}.intersection(path.parts)
    )
    relevant_paths = list(sorted(finding_paths))
    relevant_paths.extend(path for path in fallback_paths if path not in finding_paths)
    files: dict[str, str] = {}
    file_hashes: dict[str, str] = {}
    redaction_counts: dict[str, int] = {}
    total_bytes = 0
    for absolute_path in relevant_paths[:20]:
        relative = absolute_path.relative_to(worktree)
        with absolute_path.open(encoding="utf-8") as stream:
            original = stream.read(30_000)
        redacted = redact_secrets(original)
        encoded_size = len(redacted.content.encode("utf-8"))
        if total_bytes + encoded_size > 120_000:
            break
        relative_name = relative.as_posix()
        files[relative_name] = redacted.content
        file_hashes[relative_name] = _sha256_file(absolute_path)
        redaction_counts[relative_name] = redacted.redaction_count
        total_bytes += encoded_size
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings = tuple(
        sorted(
            (
                replace(item, path=Path(item.path).relative_to(worktree).as_posix())
                for item in raw_findings
            ),
            key=lambda item: (severity_order.get(item.severity, 1), item.path, item.line),
        )
    )
    rule_counts: dict[str, int] = {}
    for finding in findings:
        rule_counts[finding.rule_id] = rule_counts.get(finding.rule_id, 0) + 1
    optimization_hints = tuple(
        f"Prioritize {rule_id}: {count} occurrence(s); validate its effect in isolation."
        for rule_id, count in sorted(rule_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    language_names = {
        "py": "python",
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
    }
    detected_languages = {language_names[Path(path).suffix.lstrip(".")] for path in files}
    return OptimizationRequest(
        objective="Improve runtime or memory use without changing observable behavior.",
        language=", ".join(sorted(detected_languages)),
        findings=findings,
        files=files,
        maximum_candidates=maximum_candidates,
        file_hashes=file_hashes,
        redaction_counts=redaction_counts,
        optimization_hints=optimization_hints,
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
    maximum_rounds: int = 21,
    maximum_memory_regression_percent: float = 10.0,
    maximum_cpu_regression_percent: float = 10.0,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
    audit_logger: AuditLogger | None = None,
) -> OptimizationRun:
    repository = repository.resolve()
    commit = resolve_commit(repository, baseline_ref)
    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy()
    if audit_logger:
        audit_logger.append("optimization_started", {"baseline_commit": commit})
    with _worktree(repository, commit) as baseline_tree:
        request = _request(repository, baseline_tree, maximum_candidates)
        candidates = provider.generate(request)

    evaluations: list[CandidateEvaluation] = []
    paired_baselines: list[BenchmarkResult] = []
    for candidate in candidates:
        try:
            with _worktree(repository, commit) as baseline_tree:
                with _worktree(repository, commit) as candidate_tree:
                    changed_paths = apply_patch(candidate_tree, candidate.patch)
                    correctness = run_correctness(
                        test_command,
                        cwd=candidate_tree,
                        runner=selected_runner,
                        policy=selected_policy,
                    )
                    if not correctness:
                        evaluations.append(
                            CandidateEvaluation(
                                candidate,
                                Decision.REJECT.value,
                                None,
                                "candidate failed the correctness command",
                                changed_paths,
                                0.0,
                            )
                        )
                        if audit_logger:
                            audit_logger.append(
                                "candidate_evaluated",
                                {
                                    "candidate_id": candidate.candidate_id,
                                    "status": Decision.REJECT.value,
                                },
                            )
                        continue
                    baseline, measured = run_adaptive_paired_benchmarks(
                        benchmark_command,
                        baseline_cwd=baseline_tree,
                        candidate_cwd=candidate_tree,
                        minimum_rounds=rounds,
                        maximum_rounds=max(rounds, maximum_rounds),
                        runner=selected_runner,
                        policy=selected_policy,
                    )
                paired_baselines.append(baseline)
                result = compare(
                    baseline,
                    measured,
                    correctness_passed=correctness,
                    minimum_improvement_percent=minimum_improvement_percent,
                    paired=True,
                    maximum_memory_regression_percent=maximum_memory_regression_percent,
                    maximum_cpu_regression_percent=maximum_cpu_regression_percent,
                )
                evaluations.append(
                    CandidateEvaluation(
                        candidate,
                        result.decision.value,
                        result,
                        None,
                        changed_paths,
                        result.utility_score,
                    )
                )
                if audit_logger:
                    audit_logger.append(
                        "candidate_evaluated",
                        {"candidate_id": candidate.candidate_id, "status": result.decision.value},
                    )
        except (PatchValidationError, OSError, RuntimeError, ValueError) as exc:
            evaluations.append(CandidateEvaluation(candidate, "invalid", None, str(exc), (), 0.0))
            if audit_logger:
                audit_logger.append(
                    "candidate_invalid", {"candidate_id": candidate.candidate_id, "error": str(exc)}
                )

    accepted = [
        item for item in evaluations if item.result and item.result.decision is Decision.ACCEPT
    ]
    accepted.sort(
        key=lambda item: (
            (
                -item.utility_score,
                -item.result.speedup_ci95_low,
                item.result.candidate.peak_memory_bytes,
                item.candidate.candidate_id,
            )
            if item.result
            else (0.0, 0.0, 0, "")
        )
    )
    winner = accepted[0].candidate.candidate_id if accepted else None
    if audit_logger:
        audit_logger.append("optimization_completed", {"winner_id": winner})
    if paired_baselines:
        baseline = paired_baselines[0]
    else:
        with _worktree(repository, commit) as baseline_tree:
            baseline = run_benchmark(
                benchmark_command,
                cwd=baseline_tree,
                rounds=rounds,
                runner=selected_runner,
                policy=selected_policy,
            )
    return OptimizationRun(
        schema_version=2,
        run_id=f"opt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}",
        created_at=datetime.now(UTC).isoformat(),
        baseline_commit=commit,
        baseline=baseline,
        evaluations=tuple(evaluations),
        winner_id=winner,
        environment=environment_fingerprint(),
    )


def save_optimization(run: OptimizationRun, output_directory: Path) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / f"{run.run_id}.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{run.run_id}-", suffix=".tmp", dir=output_directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(run.to_dict(), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return destination


def export_winning_patch(run: OptimizationRun, destination: Path) -> Path | None:
    winner = next(
        (item for item in run.evaluations if item.candidate.candidate_id == run.winner_id), None
    )
    if winner is None:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(winner.candidate.patch, encoding="utf-8")
    return destination
