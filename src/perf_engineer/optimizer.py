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
from .callable_benchmark import PythonCallableTarget, run_paired_callable_benchmarks
from .environment import environment_fingerprint
from .execution import CommandRunner, ExecutionPolicy, LocalProcessRunner
from .models import BenchmarkResult, Decision, PerformanceAttribution, VerificationResult
from .patches import PatchValidationError, apply_patch
from .profiling import CProfileAdapter, ProfileResult, ProfilingError
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
    attribution: PerformanceAttribution | None = None


@dataclass(frozen=True)
class OptimizationStage:
    stage_number: int
    candidate_id: str
    baseline_commit: str
    resulting_commit: str
    incremental_speedup_percent: float
    cumulative_speedup_percent: float
    changed_paths: tuple[str, ...]


def _percent_change(baseline: float, candidate_value: float) -> float:
    if baseline <= 0:
        return 0.0
    return ((candidate_value - baseline) / baseline) * 100.0


def _evidence_label(candidate: OptimizationCandidate, request: OptimizationRequest) -> str:
    evidence: dict[str, str] = {}
    for finding in request.findings:
        evidence_id = f"finding:{finding.rule_id}:{finding.path}:{finding.line}"
        evidence[evidence_id] = (
            f"{finding.rule_id} at {finding.path}:{finding.line}: {finding.message}"
        )
    for hotspot in request.hotspots:
        evidence_id = f"hotspot:{hotspot.file}:{hotspot.line}:{hotspot.function}"
        evidence[evidence_id] = (
            f"hotspot {hotspot.file}:{hotspot.line} {hotspot.function} "
            f"({hotspot.cumulative_seconds:.6f}s cumulative)"
        )
    matched = [evidence[item] for item in candidate.target_evidence_ids if item in evidence]
    return "; ".join(matched) if matched else candidate.rationale


def _attribution(
    candidate: OptimizationCandidate,
    result: VerificationResult,
    request: OptimizationRequest,
) -> PerformanceAttribution:
    confidence = (
        "high"
        if result.stable and result.decision is not Decision.INCONCLUSIVE
        else ("medium" if result.stable else "low")
    )
    return PerformanceAttribution(
        targeted_issue=_evidence_label(candidate, request),
        strategy=candidate.strategy,
        baseline_wall_seconds=result.baseline.median_seconds,
        candidate_wall_seconds=result.candidate.median_seconds,
        wall_change_percent=_percent_change(
            result.baseline.median_seconds,
            result.candidate.median_seconds,
        ),
        baseline_cpu_seconds=result.baseline.cpu_mean_seconds,
        candidate_cpu_seconds=result.candidate.cpu_mean_seconds,
        cpu_change_percent=result.cpu_change_percent,
        baseline_peak_memory_bytes=result.baseline.peak_memory_bytes,
        candidate_peak_memory_bytes=result.candidate.peak_memory_bytes,
        memory_change_percent=result.memory_change_percent,
        correctness_passed=result.correctness_passed,
        stable=result.stable,
        confidence=confidence,
        decision=result.decision,
    )


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
    baseline_profile: ProfileResult | None = None
    provider_attempts: int = 1
    stages: tuple[OptimizationStage, ...] = ()
    composed_patch: str | None = None
    final_verification: VerificationResult | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _apply_candidate_sequence(
    worktree: Path,
    candidates: tuple[OptimizationCandidate, ...],
) -> tuple[str, ...]:
    changed_paths: list[str] = []
    for candidate in candidates:
        changed_paths.extend(apply_patch(worktree, candidate.patch))
    return tuple(dict.fromkeys(changed_paths))


def _cumulative_speedup_percent(original_seconds: float, current_seconds: float) -> float:
    if original_seconds <= 0:
        return 0.0
    return ((original_seconds - current_seconds) / original_seconds) * 100.0


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


def _request(
    repository: Path,
    worktree: Path,
    maximum_candidates: int,
    profile: ProfileResult | None = None,
) -> OptimizationRequest:
    raw_findings = tuple(analyze_path(worktree))
    finding_paths = {Path(item.path) for item in raw_findings}
    supported_suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
    fallback_paths = (
        path
        for path in sorted(worktree.rglob("*"))
        if path.suffix in supported_suffixes
        and not {".git", "node_modules", "__pycache__"}.intersection(path.parts)
    )
    hotspot_paths: list[Path] = []
    project_hotspots = []
    if profile:
        for hotspot in profile.hotspots:
            candidate = Path(hotspot.file)
            candidate = candidate if candidate.is_absolute() else worktree / candidate
            try:
                resolved = candidate.resolve()
                resolved.relative_to(worktree)
            except (OSError, ValueError):
                continue
            if resolved.is_file() and resolved.suffix in supported_suffixes:
                hotspot_paths.append(resolved)
                project_hotspots.append(
                    replace(hotspot, file=resolved.relative_to(worktree).as_posix())
                )
    relevant_paths = list(dict.fromkeys(hotspot_paths))
    relevant_paths.extend(path for path in sorted(finding_paths) if path not in relevant_paths)
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
    if project_hotspots:
        profile_hints = tuple(
            f"Measured hotspot {item.file}:{item.line} {item.function}: "
            f"{item.cumulative_seconds:.6f}s cumulative across {item.calls} call(s)."
            for item in project_hotspots[:10]
        )
        optimization_hints = profile_hints + optimization_hints
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
        hotspots=tuple(project_hotspots),
    )


def _candidate_feedback(evaluations: list[CandidateEvaluation]) -> tuple[str, ...]:
    feedback: list[str] = []
    for evaluation in evaluations:
        attribution = evaluation.attribution
        if evaluation.result and attribution:
            result = evaluation.result
            evidence_ids = ", ".join(evaluation.candidate.target_evidence_ids) or "unlinked"
            feedback.append(
                f"{evaluation.candidate.candidate_id} ({attribution.strategy}): "
                f"target={attribution.targeted_issue}; evidence={evidence_ids}; "
                f"decision={attribution.decision.value}; confidence={attribution.confidence}; "
                f"wall_change={attribution.wall_change_percent:.2f}%; "
                f"speedup_ci95=[{result.speedup_ci95_low:.2f}%, "
                f"{result.speedup_ci95_high:.2f}%]; "
                f"memory_change={attribution.memory_change_percent:.2f}%; "
                f"cpu_change={attribution.cpu_change_percent:.2f}%; "
                f"reason={result.reason}."
            )
        else:
            feedback.append(
                f"{evaluation.candidate.candidate_id} ({evaluation.candidate.strategy}): "
                f"{evaluation.status}; {evaluation.error or 'no measurement available'}."
            )
    return tuple(feedback[-20:])


def _refinement_hints(evaluations: list[CandidateEvaluation]) -> tuple[str, ...]:
    hints: list[str] = []
    for evaluation in evaluations:
        attribution = evaluation.attribution
        if not evaluation.result or not attribution:
            continue
        evidence_ids = ", ".join(evaluation.candidate.target_evidence_ids) or "unlinked evidence"
        if attribution.decision is Decision.REJECT and attribution.confidence == "high":
            hints.append(
                f"Avoid repeating strategy {attribution.strategy} for {evidence_ids}; "
                "try a materially different optimization mechanism."
            )
        elif attribution.decision is Decision.INCONCLUSIVE or attribution.confidence == "low":
            hints.append(
                f"Treat {evidence_ids} as uncertain; prefer a different hypothesis or stronger "
                "evidence rather than repeating the same patch shape."
            )
        if attribution.wall_change_percent < 0 and (
            attribution.memory_change_percent > 0 or attribution.cpu_change_percent > 0
        ):
            hints.append(
                f"For {evidence_ids}, preserve the wall-time improvement while reducing the "
                "observed CPU or memory regression."
            )
    return tuple(dict.fromkeys(hints))


def optimize(
    *,
    repository: Path,
    baseline_ref: str,
    provider: CandidateProvider,
    benchmark_command: list[str] | None,
    test_command: list[str],
    rounds: int = 7,
    maximum_candidates: int = 3,
    minimum_improvement_percent: float = 5.0,
    maximum_rounds: int = 21,
    maximum_memory_regression_percent: float = 10.0,
    maximum_cpu_regression_percent: float = 10.0,
    profile_guidance: bool = True,
    maximum_provider_attempts: int = 2,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
    audit_logger: AuditLogger | None = None,
    benchmark_callable: PythonCallableTarget | None = None,
) -> OptimizationRun:
    if maximum_provider_attempts < 1:
        raise ValueError("maximum_provider_attempts must be at least 1")
    if (benchmark_command is None) == (benchmark_callable is None):
        raise ValueError("provide exactly one benchmark command or callable")
    repository = repository.resolve()
    commit = resolve_commit(repository, baseline_ref)
    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy()
    if audit_logger:
        audit_logger.append("optimization_started", {"baseline_commit": commit})
    baseline_profile: ProfileResult | None = None
    with _worktree(repository, commit) as baseline_tree:
        if (
            profile_guidance
            and benchmark_command is not None
            and "python" in Path(benchmark_command[0]).name.lower()
        ):
            try:
                baseline_profile = CProfileAdapter(maximum_hotspots=20).profile(
                    benchmark_command, cwd=baseline_tree, policy=selected_policy
                )
            except ProfilingError:
                baseline_profile = None
        request = _request(repository, baseline_tree, maximum_candidates, baseline_profile)

    evaluations: list[CandidateEvaluation] = []
    paired_baselines: list[BenchmarkResult] = []
    accepted_sequence: list[OptimizationCandidate] = []
    stages: list[OptimizationStage] = []
    original_baseline_seconds: float | None = None
    provider_attempts = 1
    seen_patches: set[str] = set()

    while True:
        stage_evaluations: list[CandidateEvaluation] = []
        with _worktree(repository, commit) as stage_tree:
            _apply_candidate_sequence(stage_tree, tuple(accepted_sequence))
            stage_profile: ProfileResult | None = None
            if (
                profile_guidance
                and benchmark_command is not None
                and "python" in Path(benchmark_command[0]).name.lower()
            ):
                try:
                    stage_profile = CProfileAdapter(maximum_hotspots=20).profile(
                        benchmark_command, cwd=stage_tree, policy=selected_policy
                    )
                except ProfilingError:
                    stage_profile = None
            request = _request(repository, stage_tree, maximum_candidates, stage_profile)
            request = replace(
                request,
                attempt_number=provider_attempts,
                feedback=_candidate_feedback(evaluations),
                optimization_hints=request.optimization_hints + _refinement_hints(evaluations),
            )
            candidates = provider.generate(request)

        fresh_candidates: list[OptimizationCandidate] = []
        used_ids = {item.candidate.candidate_id for item in evaluations}
        for candidate in candidates:
            patch_hash = hashlib.sha256(candidate.patch.encode()).hexdigest()
            if patch_hash in seen_patches:
                continue
            seen_patches.add(patch_hash)
            if candidate.candidate_id in used_ids:
                candidate = replace(
                    candidate,
                    candidate_id=f"attempt-{provider_attempts}-{candidate.candidate_id}",
                )
            used_ids.add(candidate.candidate_id)
            fresh_candidates.append(candidate)

        for candidate in fresh_candidates:
            try:
                with _worktree(repository, commit) as baseline_tree:
                    with _worktree(repository, commit) as candidate_tree:
                        _apply_candidate_sequence(baseline_tree, tuple(accepted_sequence))
                        _apply_candidate_sequence(candidate_tree, tuple(accepted_sequence))
                        changed_paths = apply_patch(candidate_tree, candidate.patch)
                        correctness = run_correctness(
                            test_command,
                            cwd=candidate_tree,
                            runner=selected_runner,
                            policy=selected_policy,
                        )
                        if not correctness:
                            evaluation = CandidateEvaluation(
                                candidate,
                                Decision.REJECT.value,
                                None,
                                "candidate failed the correctness command",
                                changed_paths,
                                0.0,
                            )
                            evaluations.append(evaluation)
                            stage_evaluations.append(evaluation)
                            continue
                        if benchmark_callable is not None:
                            baseline, measured = run_paired_callable_benchmarks(
                                benchmark_callable,
                                baseline_cwd=baseline_tree,
                                candidate_cwd=candidate_tree,
                                minimum_rounds=rounds,
                                maximum_rounds=max(rounds, maximum_rounds),
                                minimum_improvement_percent=minimum_improvement_percent,
                                policy=selected_policy,
                            )
                        else:
                            assert benchmark_command is not None
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
                    if original_baseline_seconds is None:
                        original_baseline_seconds = baseline.median_seconds
                    result = compare(
                        baseline,
                        measured,
                        correctness_passed=correctness,
                        minimum_improvement_percent=minimum_improvement_percent,
                        paired=True,
                        maximum_memory_regression_percent=maximum_memory_regression_percent,
                        maximum_cpu_regression_percent=maximum_cpu_regression_percent,
                    )
                    evaluation = CandidateEvaluation(
                        candidate,
                        result.decision.value,
                        result,
                        None,
                        changed_paths,
                        result.utility_score,
                        _attribution(candidate, result, request),
                    )
                    evaluations.append(evaluation)
                    stage_evaluations.append(evaluation)
                    if audit_logger:
                        audit_logger.append(
                            "candidate_evaluated",
                            {
                                "candidate_id": candidate.candidate_id,
                                "status": result.decision.value,
                            },
                        )
            except (PatchValidationError, OSError, RuntimeError, ValueError) as exc:
                evaluation = CandidateEvaluation(
                    candidate, "invalid", None, str(exc), (), 0.0
                )
                evaluations.append(evaluation)
                stage_evaluations.append(evaluation)

        accepted = [
            item
            for item in stage_evaluations
            if item.result and item.result.decision is Decision.ACCEPT
        ]
        accepted.sort(
            key=lambda item: (
                -item.utility_score,
                -item.result.speedup_ci95_low if item.result else 0.0,
                item.result.candidate.peak_memory_bytes if item.result else 0,
                item.candidate.candidate_id,
            )
        )
        if not accepted:
            if provider_attempts >= maximum_provider_attempts:
                break
            provider_attempts += 1
            if audit_logger:
                audit_logger.append(
                    "provider_refined",
                    {"attempt": provider_attempts, "candidate_count": 0},
                )
            continue

        selected = accepted[0]
        assert selected.result is not None
        accepted_sequence.append(selected.candidate)
        assert original_baseline_seconds is not None
        stages.append(
            OptimizationStage(
                stage_number=len(stages) + 1,
                candidate_id=selected.candidate.candidate_id,
                baseline_commit=commit,
                resulting_commit=commit,
                incremental_speedup_percent=selected.result.speedup_percent,
                cumulative_speedup_percent=_cumulative_speedup_percent(
                    original_baseline_seconds,
                    selected.result.candidate.median_seconds,
                ),
                changed_paths=selected.changed_paths,
            )
        )
        if len(stages) >= maximum_provider_attempts:
            break
        provider_attempts += 1
        if audit_logger:
            audit_logger.append(
                "optimization_stage_promoted",
                {
                    "stage": len(stages),
                    "candidate_id": selected.candidate.candidate_id,
                },
            )

    winner = stages[-1].candidate_id if stages else None
    composed_patch: str | None = None
    final_verification: VerificationResult | None = None
    if accepted_sequence:
        with (
            _worktree(repository, commit) as original_tree,
            _worktree(repository, commit) as final_tree,
        ):
                _apply_candidate_sequence(final_tree, tuple(accepted_sequence))
                final_correctness = run_correctness(
                    test_command,
                    cwd=final_tree,
                    runner=selected_runner,
                    policy=selected_policy,
                )
                if benchmark_callable is not None:
                    final_baseline, final_candidate = run_paired_callable_benchmarks(
                        benchmark_callable,
                        baseline_cwd=original_tree,
                        candidate_cwd=final_tree,
                        minimum_rounds=rounds,
                        maximum_rounds=max(rounds, maximum_rounds),
                        minimum_improvement_percent=minimum_improvement_percent,
                        policy=selected_policy,
                    )
                else:
                    assert benchmark_command is not None
                    final_baseline, final_candidate = run_adaptive_paired_benchmarks(
                        benchmark_command,
                        baseline_cwd=original_tree,
                        candidate_cwd=final_tree,
                        minimum_rounds=rounds,
                        maximum_rounds=max(rounds, maximum_rounds),
                        runner=selected_runner,
                        policy=selected_policy,
                    )
                final_verification = compare(
                    final_baseline,
                    final_candidate,
                    correctness_passed=final_correctness,
                    minimum_improvement_percent=minimum_improvement_percent,
                    paired=True,
                    maximum_memory_regression_percent=maximum_memory_regression_percent,
                    maximum_cpu_regression_percent=maximum_cpu_regression_percent,
                )
                diff = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(final_tree),
                        "diff",
                        "--no-ext-diff",
                        "--binary",
                        "HEAD",
                        "--",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if diff.returncode:
                    raise RuntimeError(diff.stderr.strip())
                composed_patch = diff.stdout or None
    if audit_logger:
        audit_logger.append("optimization_completed", {"winner_id": winner})
    if paired_baselines:
        baseline = paired_baselines[0]
    else:
        with _worktree(repository, commit) as baseline_tree:
            if benchmark_callable is not None:
                baseline, _ = run_paired_callable_benchmarks(
                    benchmark_callable,
                    baseline_cwd=baseline_tree,
                    candidate_cwd=baseline_tree,
                    minimum_rounds=rounds,
                    maximum_rounds=max(rounds, maximum_rounds),
                            minimum_improvement_percent=minimum_improvement_percent,
                    policy=selected_policy,
                )
            else:
                assert benchmark_command is not None
                baseline = run_benchmark(
                    benchmark_command,
                    cwd=baseline_tree,
                    rounds=rounds,
                    runner=selected_runner,
                    policy=selected_policy,
                )
    return OptimizationRun(
        schema_version=6,
        run_id=f"opt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}",
        created_at=datetime.now(UTC).isoformat(),
        baseline_commit=commit,
        baseline=baseline,
        evaluations=tuple(evaluations),
        winner_id=winner,
        environment=environment_fingerprint(),
        baseline_profile=baseline_profile,
        provider_attempts=provider_attempts,
        stages=tuple(stages),
        composed_patch=composed_patch,
        final_verification=final_verification,
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
    if not run.winner_id:
        return None
    patch = run.composed_patch
    if patch is None:
        winner = next(
            (item for item in run.evaluations if item.candidate.candidate_id == run.winner_id),
            None,
        )
        patch = winner.candidate.patch if winner is not None else None
    if not patch:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(patch.rstrip() + "\n", encoding="utf-8")
    return destination
