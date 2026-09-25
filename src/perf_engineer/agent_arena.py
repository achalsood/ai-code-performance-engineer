from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .benchmark import run_adaptive_paired_benchmarks
from .evaluation import CorpusCase, load_corpus
from .execution import CommandRunner, ExecutionPolicy, LocalProcessRunner
from .models import Decision
from .patches import PatchValidationError, apply_patch
from .providers import CandidateProvider, OptimizationRequest, ProviderError
from .verification import compare, run_correctness


@dataclass(frozen=True)
class AgentCaseResult:
    case_id: str
    category: str
    language: str
    status: str
    candidate_id: str | None
    speedup_percent: float | None
    speedup_ci95_low: float | None
    error: str | None


@dataclass(frozen=True)
class AgentArenaRun:
    schema_version: int
    suite_name: str
    provider_label: str
    created_at: str
    results: tuple[AgentCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _request(case: CorpusCase, baseline: Path, maximum_candidates: int) -> OptimizationRequest:
    files: dict[str, str] = {}
    for path in sorted(baseline.rglob("*")):
        hidden_names = {
            "workload.py",
            "workload.js",
            "test_correctness.py",
            "test_correctness.js",
        }
        if path.is_file() and path.name not in hidden_names:
            files[path.relative_to(baseline).as_posix()] = path.read_text(encoding="utf-8")
    return OptimizationRequest(
        objective=(
            f"Optimize PerfArena challenge {case.case_id}: {case.description}. "
            "Improve runtime or memory without changing observable behavior."
        ),
        language=case.language,
        findings=(),
        files=files,
        maximum_candidates=maximum_candidates,
        optimization_hints=(f"Category: {case.category}",),
    )


def run_agent_arena(
    corpus_path: Path,
    *,
    provider: CandidateProvider,
    provider_label: str,
    rounds: int = 7,
    maximum_rounds: int = 21,
    maximum_candidates: int = 3,
    runner: CommandRunner | None = None,
    policy: ExecutionPolicy | None = None,
) -> AgentArenaRun:
    suite_name, cases = load_corpus(corpus_path)
    root = corpus_path.parent
    selected_runner = runner or LocalProcessRunner()
    selected_policy = policy or ExecutionPolicy()
    results: list[AgentCaseResult] = []
    for case in cases:
        baseline = (root / case.baseline_directory).resolve()
        request = _request(case, baseline, maximum_candidates)
        try:
            candidates = provider.generate(request)
        except ProviderError as exc:
            results.append(
                AgentCaseResult(
                    case.case_id,
                    case.category,
                    case.language,
                    "provider-failure",
                    None,
                    None,
                    None,
                    str(exc),
                )
            )
            continue
        best: AgentCaseResult | None = None
        for candidate in candidates:
            try:
                with tempfile.TemporaryDirectory(prefix=f"perfarena-{case.case_id}-") as directory:
                    candidate_dir = Path(directory) / "candidate"
                    shutil.copytree(baseline, candidate_dir)
                    apply_patch(candidate_dir, candidate.patch)
                    correct = run_correctness(
                        list(case.test_command),
                        cwd=candidate_dir,
                        runner=selected_runner,
                        policy=selected_policy,
                    )
                    if not correct:
                        result = AgentCaseResult(
                            case.case_id,
                            case.category,
                            case.language,
                            "correctness-failure",
                            candidate.candidate_id,
                            None,
                            None,
                            None,
                        )
                    else:
                        base_measure, candidate_measure = run_adaptive_paired_benchmarks(
                            list(case.benchmark_command),
                            baseline_cwd=baseline,
                            candidate_cwd=candidate_dir,
                            minimum_rounds=rounds,
                            maximum_rounds=max(rounds, maximum_rounds),
                            runner=selected_runner,
                            policy=selected_policy,
                        )
                        verification = compare(
                            base_measure,
                            candidate_measure,
                            correctness_passed=True,
                            minimum_improvement_percent=case.minimum_improvement_percent,
                            paired=True,
                        )
                        status = "accepted" if verification.decision is Decision.ACCEPT else "rejected"
                        result = AgentCaseResult(
                            case.case_id, case.category, case.language, status,
                            candidate.candidate_id, verification.speedup_percent,
                            verification.speedup_ci95_low, None,
                        )
            except (PatchValidationError, OSError, RuntimeError, ValueError) as exc:
                result = AgentCaseResult(
                    case.case_id,
                    case.category,
                    case.language,
                    "invalid",
                    candidate.candidate_id,
                    None,
                    None,
                    str(exc),
                )
            if best is None or (
                result.status == "accepted",
                result.speedup_ci95_low or float("-inf"),
            ) > (
                best.status == "accepted",
                best.speedup_ci95_low or float("-inf"),
            ):
                best = result
        results.append(
            best
            or AgentCaseResult(
                case.case_id,
                case.category,
                case.language,
                "provider-failure",
                None,
                None,
                None,
                "provider returned no candidates",
            )
        )
    return AgentArenaRun(
        1,
        suite_name,
        provider_label,
        datetime.now(UTC).isoformat(),
        tuple(results),
    )


def save_agent_arena(run: AgentArenaRun, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(run.to_dict(), indent=2) + "\n", encoding="utf-8")
    return destination
