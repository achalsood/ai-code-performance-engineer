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
from .models import (
    BenchmarkResult,
    Decision,
    Finding,
    PerformanceAttribution,
    VerificationResult,
)
from .patches import PatchValidationError, apply_patch
from .profiling import CProfileAdapter, Hotspot, ProfileResult, ProfilingError
from .providers import (
    CandidateProvider,
    OptimizationCandidate,
    OptimizationPlanStep,
    OptimizationRequest,
)
from .redaction import redact_secrets
from .repository import resolve_commit
from .verification import compare, run_correctness


@dataclass(frozen=True)
class OptimizationExplanation:
    summary: str
    targeted_issue: str
    strategy: str
    evidence_ids: tuple[str, ...]
    changed_paths: tuple[str, ...]
    decision: str
    reason: str
    correctness_passed: bool | None
    confidence: str
    baseline_wall_seconds: float | None = None
    candidate_wall_seconds: float | None = None
    speedup_percent: float | None = None
    speedup_ci95_low: float | None = None
    speedup_ci95_high: float | None = None
    cpu_change_percent: float | None = None
    memory_change_percent: float | None = None
    plan_priorities: tuple[int, ...] = ()


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: OptimizationCandidate
    status: str
    result: VerificationResult | None
    error: str | None
    changed_paths: tuple[str, ...]
    utility_score: float = 0.0
    attribution: PerformanceAttribution | None = None
    baseline_state: str | None = None
    stage_number: int | None = None
    attempt_number: int | None = None
    explanation: OptimizationExplanation | None = None
    plan_priorities: tuple[int, ...] = ()


@dataclass(frozen=True)
class OptimizationStage:
    stage_number: int
    candidate_id: str
    baseline_state: str
    resulting_state: str
    incremental_speedup_percent: float
    cumulative_speedup_percent: float
    changed_paths: tuple[str, ...]
    promotion_reason: str = ""
    alternatives_considered: tuple[str, ...] = ()
    plan_priorities: tuple[int, ...] = ()
    evidence_ids: tuple[str, ...] = ()


def _percent_change(baseline: float, candidate_value: float) -> float:
    if baseline <= 0:
        return 0.0
    return ((candidate_value - baseline) / baseline) * 100.0


def _plan_priorities(
    candidate: OptimizationCandidate,
    request: OptimizationRequest,
) -> tuple[int, ...]:
    priorities = {
        step.evidence_id: step.priority
        for step in request.plan
    }
    return tuple(
        sorted(
            {
                priorities[evidence_id]
                for evidence_id in candidate.target_evidence_ids
                if evidence_id in priorities
            }
        )
    )


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


def _explanation(
    candidate: OptimizationCandidate,
    request: OptimizationRequest,
    *,
    decision: str,
    reason: str,
    changed_paths: tuple[str, ...],
    result: VerificationResult | None = None,
    attribution: PerformanceAttribution | None = None,
    correctness_passed: bool | None = None,
) -> OptimizationExplanation:
    targeted_issue = (
        attribution.targeted_issue if attribution else _evidence_label(candidate, request)
    )
    confidence = attribution.confidence if attribution else "unmeasured"
    if result is not None:
        correctness_passed = result.correctness_passed
        summary = (
            f"{decision.upper()}: {candidate.strategy} targeted {targeted_issue}; "
            f"measured {result.speedup_percent:.2f}% speedup "
            f"(95% CI {result.speedup_ci95_low:.2f}% to {result.speedup_ci95_high:.2f}%). "
            f"{reason}"
        )
    else:
        summary = (
            f"{decision.upper()}: {candidate.strategy} targeted {targeted_issue}; "
            f"performance was not measured. {reason}"
        )
    return OptimizationExplanation(
        summary=summary,
        targeted_issue=targeted_issue,
        strategy=candidate.strategy,
        evidence_ids=candidate.target_evidence_ids,
        changed_paths=changed_paths,
        decision=decision,
        reason=reason,
        correctness_passed=correctness_passed,
        confidence=confidence,
        baseline_wall_seconds=(
            result.baseline.median_seconds if result is not None else None
        ),
        candidate_wall_seconds=(
            result.candidate.median_seconds if result is not None else None
        ),
        speedup_percent=result.speedup_percent if result is not None else None,
        speedup_ci95_low=result.speedup_ci95_low if result is not None else None,
        speedup_ci95_high=result.speedup_ci95_high if result is not None else None,
        cpu_change_percent=result.cpu_change_percent if result is not None else None,
        memory_change_percent=(
            result.memory_change_percent if result is not None else None
        ),
        plan_priorities=_plan_priorities(candidate, request),
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
    provider_attempts: int = 0
    stages: tuple[OptimizationStage, ...] = ()
    composed_patch: str | None = None
    final_verification: VerificationResult | None = None
    explanation: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_explanation(
    stages: tuple[OptimizationStage, ...],
    evaluations: tuple[CandidateEvaluation, ...],
    final_verification: VerificationResult | None,
) -> str:
    if not stages:
        invalid = sum(item.status == "invalid" for item in evaluations)
        rejected = sum(item.status == Decision.REJECT.value for item in evaluations)
        inconclusive = sum(
            item.status == Decision.INCONCLUSIVE.value for item in evaluations
        )
        return (
            "No optimization was promoted. "
            f"Evaluated {len(evaluations)} candidate(s): {rejected} rejected, "
            f"{inconclusive} inconclusive, {invalid} invalid."
        )

    promoted = " -> ".join(stage.candidate_id for stage in stages)
    if final_verification is None:
        return (
            f"Promoted {len(stages)} stage(s): {promoted}. "
            "Final original-to-optimized verification is unavailable."
        )
    return (
        f"Promoted {len(stages)} stage(s): {promoted}. Final verification "
        f"{final_verification.decision.value}: "
        f"{final_verification.speedup_percent:.2f}% speedup "
        f"(95% CI {final_verification.speedup_ci95_low:.2f}% to "
        f"{final_verification.speedup_ci95_high:.2f}%), "
        f"CPU change {final_verification.cpu_change_percent:.2f}%, "
        f"memory change {final_verification.memory_change_percent:.2f}%; "
        f"correctness={'passed' if final_verification.correctness_passed else 'failed'}. "
        f"{final_verification.reason}"
    )


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


def _optimization_state_id(worktree: Path) -> str:
    """Return the Git tree identity of the current repository state."""
    with tempfile.TemporaryDirectory(prefix="perf-engineer-state-") as directory:
        index_path = Path(directory) / "index"
        environment = os.environ.copy()
        environment["GIT_INDEX_FILE"] = str(index_path)

        for arguments in (("read-tree", "HEAD"), ("add", "-A", "--", ".")):
            result = subprocess.run(
                ["git", "-C", str(worktree), *arguments],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip())

        result = subprocess.run(
            ["git", "-C", str(worktree), "write-tree"],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        tree_id = result.stdout.strip()
        if not tree_id:
            raise RuntimeError("git write-tree returned an empty tree identity")
        return f"state:{tree_id}"


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


def _optimization_plan(
    findings: tuple[Finding, ...],
    hotspots: tuple[Hotspot, ...],
) -> tuple[OptimizationPlanStep, ...]:
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    hotspot_by_file: dict[str, tuple[Hotspot, ...]] = {}
    for hotspot in hotspots:
        hotspot_by_file.setdefault(hotspot.file, ())
        hotspot_by_file[hotspot.file] += (hotspot,)

    ranked_findings: list[tuple[int, int, str, int, Finding, Hotspot | None]] = []
    for finding in findings:
        nearby = [
            hotspot
            for hotspot in hotspot_by_file.get(finding.path, ())
            if abs(hotspot.line - finding.line) <= 5
        ]
        nearest = min(
            nearby,
            key=lambda hotspot: (
                abs(hotspot.line - finding.line),
                -hotspot.cumulative_seconds,
                hotspot.function,
            ),
            default=None,
        )
        ranked_findings.append(
            (
                0 if nearest is not None else 1,
                severity_rank.get(finding.severity, 1),
                finding.path,
                finding.line,
                finding,
                nearest,
            )
        )

    steps: list[OptimizationPlanStep] = []
    represented_hotspots: set[str] = set()
    for _, _, _, _, finding, matched_hotspot in sorted(
        ranked_findings, key=lambda item: item[:4]
    ):
        evidence_id = f"finding:{finding.rule_id}:{finding.path}:{finding.line}"
        if matched_hotspot is None:
            rationale = f"{finding.severity} severity: {finding.message}"
        else:
            hotspot_id = (
                f"hotspot:{matched_hotspot.file}:{matched_hotspot.line}:"
                f"{matched_hotspot.function}"
            )
            represented_hotspots.add(hotspot_id)
            rationale = (
                f"{finding.severity} severity finding near measured hotspot "
                f"{matched_hotspot.file}:{matched_hotspot.line} {matched_hotspot.function} "
                f"({matched_hotspot.cumulative_seconds:.6f}s cumulative)."
            )
        steps.append(
            OptimizationPlanStep(
                priority=len(steps) + 1,
                evidence_id=evidence_id,
                rationale=rationale,
                expected_strategy=finding.suggestion,
            )
        )

    for hotspot in hotspots:
        evidence_id = f"hotspot:{hotspot.file}:{hotspot.line}:{hotspot.function}"
        if evidence_id in represented_hotspots:
            continue
        steps.append(
            OptimizationPlanStep(
                priority=len(steps) + 1,
                evidence_id=evidence_id,
                rationale=(
                    f"Measured hotspot with {hotspot.cumulative_seconds:.6f}s cumulative "
                    f"across {hotspot.calls} call(s)."
                ),
                expected_strategy="reduce measured hot-path work",
            )
        )
    return tuple(steps[:10])


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
        plan=_optimization_plan(findings, tuple(project_hotspots)),
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
    maximum_optimization_stages: int = 3,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
    audit_logger: AuditLogger | None = None,
    benchmark_callable: PythonCallableTarget | None = None,
) -> OptimizationRun:
    if maximum_provider_attempts < 1:
        raise ValueError("maximum_provider_attempts must be at least 1")
    if maximum_optimization_stages < 1:
        raise ValueError("maximum_optimization_stages must be at least 1")
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
    provider_attempts = 0
    seen_resulting_states: set[tuple[str, str]] = set()

    for stage_number in range(1, maximum_optimization_stages + 1):
        stage_evaluations: list[CandidateEvaluation] = []
        promoted = False
        with _worktree(repository, commit) as state_tree:
            _apply_candidate_sequence(state_tree, tuple(accepted_sequence))
            baseline_state = _optimization_state_id(state_tree)

        for stage_attempt in range(1, maximum_provider_attempts + 1):
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
                            benchmark_command,
                            cwd=stage_tree,
                            policy=selected_policy,
                        )
                    except ProfilingError:
                        stage_profile = None
                request = _request(repository, stage_tree, maximum_candidates, stage_profile)
                request = replace(
                    request,
                    attempt_number=stage_attempt,
                    feedback=_candidate_feedback(stage_evaluations),
                    optimization_hints=(
                        request.optimization_hints + _refinement_hints(stage_evaluations)
                    ),
                )
                provider_attempts += 1
                candidates = provider.generate(request)

            fresh_candidates: list[OptimizationCandidate] = []
            used_ids = {item.candidate.candidate_id for item in evaluations}
            for candidate in candidates:
                try:
                    with _worktree(repository, commit) as candidate_state_tree:
                        _apply_candidate_sequence(
                            candidate_state_tree, tuple(accepted_sequence)
                        )
                        apply_patch(candidate_state_tree, candidate.patch)
                        candidate_state = _optimization_state_id(candidate_state_tree)
                except (PatchValidationError, RuntimeError):
                    # Invalid patches still need to reach evaluation so their
                    # concrete failure is preserved as refinement feedback.
                    candidate_state = None
                if candidate_state is not None:
                    state_key = (baseline_state, candidate_state)
                    if state_key in seen_resulting_states:
                        continue
                    seen_resulting_states.add(state_key)
                if candidate.candidate_id in used_ids:
                    candidate = replace(
                        candidate,
                        candidate_id=(
                            f"stage-{stage_number}-attempt-{stage_attempt}-"
                            f"{candidate.candidate_id}"
                        ),
                    )
                used_ids.add(candidate.candidate_id)
                fresh_candidates.append(candidate)

            # A provider that has no new hypothesis for the current promoted
            # state has exhausted this stage; retrying identical input cannot
            # create useful refinement feedback.
            if not fresh_candidates:
                break

            attempt_evaluations: list[CandidateEvaluation] = []
            for candidate in fresh_candidates:
                try:
                    with _worktree(repository, commit) as baseline_tree:
                        with _worktree(repository, commit) as candidate_tree:
                            _apply_candidate_sequence(
                                baseline_tree, tuple(accepted_sequence)
                            )
                            _apply_candidate_sequence(
                                candidate_tree, tuple(accepted_sequence)
                            )
                            changed_paths = apply_patch(candidate_tree, candidate.patch)
                            correctness = run_correctness(
                                test_command,
                                cwd=candidate_tree,
                                runner=selected_runner,
                                policy=selected_policy,
                            )
                            if not correctness:
                                reason = "candidate failed the correctness command"
                                evaluation = CandidateEvaluation(
                                    candidate,
                                    Decision.REJECT.value,
                                    None,
                                    reason,
                                    changed_paths,
                                    0.0,
                                    baseline_state=baseline_state,
                                    stage_number=stage_number,
                                    attempt_number=stage_attempt,
                                    explanation=_explanation(
                                        candidate,
                                        request,
                                        decision=Decision.REJECT.value,
                                        reason=reason,
                                        changed_paths=changed_paths,
                                        correctness_passed=False,
                                    ),
                                    plan_priorities=_plan_priorities(candidate, request),
                                )
                                evaluations.append(evaluation)
                                stage_evaluations.append(evaluation)
                                attempt_evaluations.append(evaluation)
                                continue
                            if benchmark_callable is not None:
                                baseline, measured = run_paired_callable_benchmarks(
                                    benchmark_callable,
                                    baseline_cwd=baseline_tree,
                                    candidate_cwd=candidate_tree,
                                    minimum_rounds=rounds,
                                    maximum_rounds=max(rounds, maximum_rounds),
                                    minimum_improvement_percent=(
                                        minimum_improvement_percent
                                    ),
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
                            maximum_memory_regression_percent=(
                                maximum_memory_regression_percent
                            ),
                            maximum_cpu_regression_percent=maximum_cpu_regression_percent,
                        )
                        attribution = _attribution(candidate, result, request)
                        evaluation = CandidateEvaluation(
                            candidate,
                            result.decision.value,
                            result,
                            None,
                            changed_paths,
                            result.utility_score,
                            attribution,
                            baseline_state=baseline_state,
                            stage_number=stage_number,
                            attempt_number=stage_attempt,
                            explanation=_explanation(
                                candidate,
                                request,
                                decision=result.decision.value,
                                reason=result.reason,
                                changed_paths=changed_paths,
                                result=result,
                                attribution=attribution,
                            ),
                            plan_priorities=_plan_priorities(candidate, request),
                        )
                        evaluations.append(evaluation)
                        stage_evaluations.append(evaluation)
                        attempt_evaluations.append(evaluation)
                        if audit_logger:
                            audit_logger.append(
                                "candidate_evaluated",
                                {
                                    "candidate_id": candidate.candidate_id,
                                    "status": result.decision.value,
                                },
                            )
                except (PatchValidationError, OSError, RuntimeError, ValueError) as exc:
                    reason = str(exc)
                    evaluation = CandidateEvaluation(
                        candidate,
                        "invalid",
                        None,
                        reason,
                        (),
                        0.0,
                        baseline_state=baseline_state,
                        stage_number=stage_number,
                        attempt_number=stage_attempt,
                        explanation=_explanation(
                            candidate,
                            request,
                            decision="invalid",
                            reason=reason,
                            changed_paths=(),
                        ),
                        plan_priorities=_plan_priorities(candidate, request),
                    )
                    evaluations.append(evaluation)
                    stage_evaluations.append(evaluation)
                    attempt_evaluations.append(evaluation)

            accepted = [
                item
                for item in attempt_evaluations
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
                if stage_attempt < maximum_provider_attempts and audit_logger:
                    audit_logger.append(
                        "provider_refined",
                        {
                            "stage": stage_number,
                            "attempt": stage_attempt + 1,
                            "candidate_count": len(fresh_candidates),
                        },
                    )
                continue

            selected = accepted[0]
            assert selected.result is not None
            ranked_alternatives = tuple(
                item.candidate.candidate_id for item in accepted
            )
            promotion_reason = (
                f"Promoted {selected.candidate.candidate_id} from "
                f"{len(accepted)} accepted candidate(s): highest utility score "
                f"({selected.utility_score:.4f}), then speedup CI lower bound "
                f"({selected.result.speedup_ci95_low:.2f}%), memory use, and "
                "candidate ID as deterministic tie-breakers."
            )
            accepted_sequence.append(selected.candidate)
            with _worktree(repository, commit) as resulting_tree:
                _apply_candidate_sequence(resulting_tree, tuple(accepted_sequence))
                resulting_state = _optimization_state_id(resulting_tree)
            assert original_baseline_seconds is not None
            stages.append(
                OptimizationStage(
                    stage_number=stage_number,
                    candidate_id=selected.candidate.candidate_id,
                    baseline_state=baseline_state,
                    resulting_state=resulting_state,
                    incremental_speedup_percent=selected.result.speedup_percent,
                    cumulative_speedup_percent=_cumulative_speedup_percent(
                        original_baseline_seconds,
                        selected.result.candidate.median_seconds,
                    ),
                    changed_paths=selected.changed_paths,
                    promotion_reason=promotion_reason,
                    alternatives_considered=ranked_alternatives,
                    plan_priorities=selected.plan_priorities,
                    evidence_ids=selected.candidate.target_evidence_ids,
                )
            )
            if audit_logger:
                audit_logger.append(
                    "optimization_stage_promoted",
                    {
                        "stage": stage_number,
                        "candidate_id": selected.candidate.candidate_id,
                    },
                )
            promoted = True
            break

        if not promoted:
            break

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
        schema_version=9,
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
        explanation=_run_explanation(
            tuple(stages), tuple(evaluations), final_verification
        ),
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
