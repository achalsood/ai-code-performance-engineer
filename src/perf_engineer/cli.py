from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .agent_arena import run_agent_arena, save_agent_arena
from .analyzer import analyze_path
from .audit import AuditLogger
from .benchmark import BenchmarkError, run_adaptive_paired_benchmarks, run_benchmark
from .evaluation import evaluate_corpus
from .execution import DockerRunner, ExecutionPolicy, LocalProcessRunner
from .experiments import run_experiment, save_record
from .fixers import DeterministicFixProvider
from .history import append_run, detect_regressions, read_runs
from .optimizer import export_winning_patch, optimize, save_optimization
from .profiling import CProfileAdapter, ProfilingError, ResourceProfiler
from .providers import (
    CandidateProvider,
    CommandProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
)
from .reporting import markdown_report
from .repository import RepositoryError
from .sarif import findings_to_sarif
from .verification import compare, run_correctness


def _command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise argparse.ArgumentTypeError("command cannot be empty")
    return command


def _fixture_repository(source: Path) -> tempfile.TemporaryDirectory[str]:
    if not source.is_dir():
        raise ValueError(f"fixture directory does not exist: {source}")
    temporary = tempfile.TemporaryDirectory(prefix="perf-fixture-")
    destination = Path(temporary.name)
    try:
        for item in source.iterdir():
            if item.is_file():
                (destination / item.name).write_bytes(item.read_bytes())
        import subprocess

        subprocess.run(["git", "init", "-q", str(destination)], check=True)
        subprocess.run(
            ["git", "-C", str(destination), "config", "user.email", "fixture@local"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(destination), "config", "user.name", "Perf Fixture"],
            check=True,
        )
        subprocess.run(["git", "-C", str(destination), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(destination), "commit", "-qm", "fixture baseline"],
            check=True,
        )
    except BaseException:
        temporary.cleanup()
        raise
    return temporary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="perf-engineer")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="action", required=True)
    analyze = subparsers.add_parser("analyze", help="find static performance risks")
    analyze.add_argument("path", type=Path)
    analyze.add_argument("--json", action="store_true")
    analyze.add_argument("--format", choices=("text", "json", "sarif"), default="text")
    analyze.add_argument("--output", type=Path)
    analyze.add_argument("--fail-on", choices=("none", "medium", "high"), default="none")

    benchmark = subparsers.add_parser("benchmark", help="measure a command")
    benchmark.add_argument("command", type=_command)
    benchmark.add_argument("--cwd", type=Path, default=Path.cwd())
    benchmark.add_argument("--rounds", type=int, default=7)
    benchmark.add_argument("--warmups", type=int, default=2)
    benchmark.add_argument("--timeout", type=float, default=30.0)

    verify = subparsers.add_parser("verify", help="compare baseline and candidate worktrees")
    verify.add_argument("--baseline", type=Path, required=True)
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--benchmark", type=_command, required=True)
    verify.add_argument("--test", type=_command, required=True)
    verify.add_argument("--rounds", type=int, default=7)
    verify.add_argument("--minimum-improvement", type=float, default=5.0)

    calibrate = subparsers.add_parser(
        "calibrate", help="inspect adaptive benchmark calibration between two worktrees"
    )
    calibrate.add_argument("--baseline", type=Path, required=True)
    calibrate.add_argument("--candidate", type=Path, required=True)
    calibrate.add_argument("--benchmark", type=_command, required=True)
    calibrate.add_argument("--rounds", type=int, default=7)
    calibrate.add_argument("--maximum-rounds", type=int, default=21)
    calibrate.add_argument("--warmups", type=int, default=2)

    experiment = subparsers.add_parser(
        "experiment", help="run a reproducible comparison between two Git revisions"
    )
    experiment.add_argument("--repository", type=Path, default=Path.cwd())
    experiment.add_argument("--baseline-ref", required=True)
    experiment.add_argument("--candidate-ref", required=True)
    experiment.add_argument("--benchmark", type=_command, required=True)
    experiment.add_argument("--test", type=_command, required=True)
    experiment.add_argument("--rounds", type=int, default=7)
    experiment.add_argument("--minimum-improvement", type=float, default=5.0)
    experiment.add_argument("--output", type=Path, default=Path(".perf-engineer/experiments"))

    optimize_parser = subparsers.add_parser(
        "optimize", help="generate, verify, and rank AI optimization candidates"
    )
    optimize_parser.add_argument("--repository", type=Path, default=Path.cwd())
    optimize_parser.add_argument("--baseline-ref", default="HEAD")
    provider_group = optimize_parser.add_mutually_exclusive_group(required=True)
    provider_group.add_argument("--provider-command", type=_command)
    provider_group.add_argument("--provider", choices=("openai", "ollama"))
    optimize_parser.add_argument("--model")
    optimize_parser.add_argument("--provider-base-url")
    optimize_parser.add_argument("--benchmark", type=_command, required=True)
    optimize_parser.add_argument("--test", type=_command, required=True)
    optimize_parser.add_argument("--rounds", type=int, default=7)
    optimize_parser.add_argument("--maximum-rounds", type=int, default=21)
    optimize_parser.add_argument("--maximum-candidates", type=int, default=3)
    optimize_parser.add_argument("--minimum-improvement", type=float, default=5.0)
    optimize_parser.add_argument("--maximum-memory-regression", type=float, default=10.0)
    optimize_parser.add_argument("--maximum-cpu-regression", type=float, default=10.0)
    optimize_parser.add_argument("--profile-guidance", choices=("auto", "off"), default="auto")
    optimize_parser.add_argument("--maximum-provider-attempts", type=int, default=2)
    optimize_parser.add_argument("--sandbox", choices=("local", "docker"), default="local")
    optimize_parser.add_argument("--docker-image", default="python:3.12-slim")
    optimize_parser.add_argument("--timeout", type=float, default=30.0)
    optimize_parser.add_argument("--memory-mb", type=int, default=1024)
    optimize_parser.add_argument(
        "--audit-log", type=Path, default=Path(".perf-engineer/audit.jsonl")
    )
    optimize_parser.add_argument(
        "--output", type=Path, default=Path(".perf-engineer/optimizations")
    )
    optimize_parser.add_argument(
        "--output-patch", type=Path, default=Path(".perf-engineer/winner.patch")
    )

    fix = subparsers.add_parser(
        "fix", help="generate and verify deterministic performance fixes"
    )
    fix.add_argument("--repository", type=Path, default=Path.cwd())
    fix.add_argument(
        "--fixture",
        type=Path,
        help="copy a benchmark fixture into a temporary Git repository before optimizing",
    )
    fix.add_argument("--baseline-ref", default="HEAD")
    fix.add_argument("--benchmark", type=_command, required=True)
    fix.add_argument("--test", type=_command, required=True)
    fix.add_argument("--rounds", type=int, default=7)
    fix.add_argument("--maximum-rounds", type=int, default=21)
    fix.add_argument("--maximum-candidates", type=int, default=3)
    fix.add_argument("--minimum-improvement", type=float, default=5.0)
    fix.add_argument("--maximum-memory-regression", type=float, default=10.0)
    fix.add_argument("--maximum-cpu-regression", type=float, default=10.0)
    fix.add_argument("--timeout", type=float, default=30.0)
    fix.add_argument("--memory-mb", type=int, default=1024)
    fix.add_argument("--output", type=Path, default=Path(".perf-engineer/fixes"))
    fix.add_argument(
        "--output-patch", type=Path, default=Path(".perf-engineer/fix.patch")
    )
    fix.add_argument(
        "--calibration-summary",
        action="store_true",
        help="print a compact adaptive calibration summary after the optimization result",
    )

    arena = subparsers.add_parser("arena", help="run a provider against PerfArena")
    arena.add_argument("--corpus", type=Path, required=True)
    arena_provider = arena.add_mutually_exclusive_group(required=True)
    arena_provider.add_argument("--provider-command", type=_command)
    arena_provider.add_argument("--provider", choices=("openai", "ollama"))
    arena.add_argument("--model")
    arena.add_argument("--provider-base-url")
    arena.add_argument("--provider-label")
    arena.add_argument("--rounds", type=int, default=7)
    arena.add_argument("--maximum-rounds", type=int, default=21)
    arena.add_argument("--maximum-candidates", type=int, default=3)
    arena.add_argument("--output", type=Path, default=Path(".perf-engineer/perfarena-agent.json"))

    evaluate = subparsers.add_parser("evaluate", help="run a reproducible optimization corpus")
    evaluate.add_argument("--corpus", type=Path, required=True)
    evaluate.add_argument("--rounds", type=int, default=7)
    evaluate.add_argument("--history", type=Path, default=Path(".perf-engineer/history.jsonl"))
    evaluate.add_argument("--report", type=Path, default=Path(".perf-engineer/report.md"))
    evaluate.add_argument("--regression-tolerance", type=float, default=5.0)

    profile = subparsers.add_parser("profile", help="collect normalized performance profiles")
    profile.add_argument("command", type=_command)
    profile.add_argument("--cwd", type=Path, default=Path.cwd())
    profile.add_argument("--adapter", choices=("resource", "cprofile"), default="resource")
    profile.add_argument("--timeout", type=float, default=30.0)
    profile.add_argument("--memory-mb", type=int, default=1024)
    profile.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "analyze":
            findings = analyze_path(args.path)
            output_format = "json" if args.json else args.format
            if output_format == "sarif":
                rendered = json.dumps(findings_to_sarif(findings), indent=2)
            elif output_format == "json":
                rendered = json.dumps([asdict(item) for item in findings], indent=2)
            else:
                rendered = "\n".join(
                    f"{item.path}:{item.line}: {item.severity} {item.rule_id} {item.message}"
                    for item in findings
                )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + ("\n" if rendered else ""), encoding="utf-8")
            elif rendered:
                print(rendered)
            threshold = {"none": 99, "medium": 1, "high": 2}[args.fail_on]
            severity = {"low": 0, "medium": 1, "high": 2}
            return 4 if any(severity.get(item.severity, 1) >= threshold for item in findings) else 0
        if args.action == "benchmark":
            benchmark_result = run_benchmark(
                args.command,
                cwd=args.cwd,
                rounds=args.rounds,
                warmups=args.warmups,
                timeout=args.timeout,
            )
            print(json.dumps(asdict(benchmark_result), indent=2))
            return 0

        if args.action == "calibrate":
            baseline, candidate = run_adaptive_paired_benchmarks(
                args.benchmark,
                baseline_cwd=args.baseline,
                candidate_cwd=args.candidate,
                minimum_rounds=args.rounds,
                maximum_rounds=max(args.rounds, args.maximum_rounds),
                warmups=args.warmups,
            )
            calibration_payload: dict[str, Any] = {
                "calibration": {
                    "probe_seconds": baseline.calibration_probe_seconds,
                    "repetitions_per_sample": baseline.repetitions_per_sample,
                    "measurement_rounds": baseline.measurement_rounds,
                    "total_measurement_seconds": baseline.total_measurement_seconds,
                },
                "baseline": asdict(baseline),
                "candidate": asdict(candidate),
            }
            print(json.dumps(calibration_payload, indent=2))
            return 0

        if args.action == "profile":
            profiler = CProfileAdapter() if args.adapter == "cprofile" else ResourceProfiler()
            profile_result = profiler.profile(
                args.command,
                cwd=args.cwd,
                policy=ExecutionPolicy(
                    timeout_seconds=args.timeout,
                    memory_bytes=args.memory_mb * 1024 * 1024,
                ),
            )
            serialized = json.dumps(profile_result.to_dict(), indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(serialized + "\n", encoding="utf-8")
            print(serialized)
            return 0

        if args.action == "experiment":
            record = run_experiment(
                repository=args.repository,
                baseline_ref=args.baseline_ref,
                candidate_ref=args.candidate_ref,
                benchmark_command=args.benchmark,
                test_command=args.test,
                rounds=args.rounds,
                minimum_improvement_percent=args.minimum_improvement,
            )
            destination = save_record(record, args.output)
            experiment_payload: dict[str, Any] = {
                **record.to_dict(),
                "record_path": str(destination),
            }
            print(json.dumps(experiment_payload, indent=2))
            return 0 if record.result.decision == "accept" else 2

        if args.action == "optimize":
            runner = (
                DockerRunner(args.docker_image)
                if args.sandbox == "docker"
                else LocalProcessRunner()
            )
            policy = ExecutionPolicy(
                timeout_seconds=args.timeout, memory_bytes=args.memory_mb * 1024 * 1024
            )
            provider: CandidateProvider
            if args.provider_command:
                provider = CommandProvider(args.provider_command)
            elif not args.model:
                raise ValueError("--model is required with a built-in provider")
            elif args.provider == "openai":
                provider = OpenAICompatibleProvider(
                    model=args.model,
                    base_url=args.provider_base_url or "https://api.openai.com/v1",
                )
            else:
                provider = OllamaProvider(
                    model=args.model,
                    base_url=args.provider_base_url or "http://127.0.0.1:11434",
                )
            optimization = optimize(
                repository=args.repository,
                baseline_ref=args.baseline_ref,
                provider=provider,
                benchmark_command=args.benchmark,
                test_command=args.test,
                rounds=args.rounds,
                maximum_candidates=args.maximum_candidates,
                minimum_improvement_percent=args.minimum_improvement,
                maximum_rounds=args.maximum_rounds,
                maximum_memory_regression_percent=args.maximum_memory_regression,
                maximum_cpu_regression_percent=args.maximum_cpu_regression,
                profile_guidance=args.profile_guidance == "auto",
                maximum_provider_attempts=args.maximum_provider_attempts,
                runner=runner,
                policy=policy,
                audit_logger=AuditLogger(args.audit_log),
            )
            record_path = save_optimization(optimization, args.output)
            patch_path = export_winning_patch(optimization, args.output_patch)
            optimization_payload: dict[str, Any] = {
                **optimization.to_dict(),
                "record_path": str(record_path),
                "winner_patch_path": str(patch_path) if patch_path else None,
            }
            print(json.dumps(optimization_payload, indent=2))
            return 0 if optimization.winner_id else 2

        if args.action == "fix":
            policy = ExecutionPolicy(
                timeout_seconds=args.timeout, memory_bytes=args.memory_mb * 1024 * 1024
            )
            fixture_repository = _fixture_repository(args.fixture) if args.fixture else None
            try:
                optimization = optimize(
                    repository=(
                        Path(fixture_repository.name)
                        if fixture_repository is not None
                        else args.repository
                    ),
                    baseline_ref=args.baseline_ref,
                    provider=DeterministicFixProvider(),
                benchmark_command=args.benchmark,
                test_command=args.test,
                rounds=args.rounds,
                maximum_candidates=args.maximum_candidates,
                minimum_improvement_percent=args.minimum_improvement,
                maximum_rounds=args.maximum_rounds,
                maximum_memory_regression_percent=args.maximum_memory_regression,
                maximum_cpu_regression_percent=args.maximum_cpu_regression,
                profile_guidance=True,
                maximum_provider_attempts=1,
                runner=LocalProcessRunner(),
                policy=policy,
            )
            finally:
                if fixture_repository is not None:
                    fixture_repository.cleanup()
            record_path = save_optimization(optimization, args.output)
            patch_path = export_winning_patch(optimization, args.output_patch)
            fix_payload: dict[str, Any] = {
                **optimization.to_dict(),
                "record_path": str(record_path),
                "winner_patch_path": str(patch_path) if patch_path else None,
            }
            print(json.dumps(fix_payload, indent=2))
            if args.calibration_summary:
                measured = next(
                    (
                        item.result
                        for item in optimization.evaluations
                        if item.result is not None
                    ),
                    None,
                )
                if measured is not None:
                    baseline = measured.baseline
                    print("\nAdaptive benchmark calibration")
                    print(
                        f"Probe duration:          "
                        f"{baseline.calibration_probe_seconds or 0.0:.6f} s"
                    )
                    print(
                        f"Repetitions per sample:  {baseline.repetitions_per_sample}"
                    )
                    print(f"Measurement rounds:      {baseline.measurement_rounds or 0}")
                    print(
                        f"Total measured duration: "
                        f"{baseline.total_measurement_seconds or 0.0:.6f} s"
                    )
                    print("\nVerification")
                    print(f"Median speedup:          {measured.speedup_percent:.2f}%")
                    print(
                        f"95% CI:                  "
                        f"[{measured.speedup_ci95_low:.2f}%, "
                        f"{measured.speedup_ci95_high:.2f}%]"
                    )
                    print(f"Decision:                {measured.decision.value.upper()}")
            return 0 if optimization.winner_id else 2

        if args.action == "arena":
            arena_provider_instance: CandidateProvider
            if args.provider_command:
                arena_provider_instance = CommandProvider(args.provider_command)
                provider_label = args.provider_label or "command-provider"
            elif not args.model:
                raise ValueError("--model is required with a built-in provider")
            elif args.provider == "openai":
                arena_provider_instance = OpenAICompatibleProvider(
                    model=args.model,
                    base_url=args.provider_base_url or "https://api.openai.com/v1",
                )
                provider_label = args.provider_label or f"openai:{args.model}"
            else:
                arena_provider_instance = OllamaProvider(
                    model=args.model,
                    base_url=args.provider_base_url or "http://127.0.0.1:11434",
                )
                provider_label = args.provider_label or f"ollama:{args.model}"
            arena_run = run_agent_arena(
                args.corpus,
                provider=arena_provider_instance,
                provider_label=provider_label,
                rounds=args.rounds,
                maximum_rounds=args.maximum_rounds,
                maximum_candidates=args.maximum_candidates,
            )
            destination = save_agent_arena(arena_run, args.output)
            print(json.dumps({**arena_run.to_dict(), "record_path": str(destination)}, indent=2))
            return 0

        if args.action == "evaluate":
            previous_runs = read_runs(args.history)
            evaluation = evaluate_corpus(args.corpus, rounds=args.rounds)
            regressions = (
                detect_regressions(
                    previous_runs[-1],
                    evaluation,
                    tolerance_percent=args.regression_tolerance,
                )
                if previous_runs
                else []
            )
            append_run(args.history, evaluation)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(markdown_report(evaluation, regressions), encoding="utf-8")
            evaluation_payload: dict[str, Any] = {
                **evaluation.to_dict(),
                "regressions": [asdict(item) for item in regressions],
                "report_path": str(args.report),
            }
            print(json.dumps(evaluation_payload, indent=2))
            return 3 if regressions else 0

        correctness = run_correctness(args.test, cwd=args.candidate)
        baseline = run_benchmark(args.benchmark, cwd=args.baseline, rounds=args.rounds)
        candidate = run_benchmark(args.benchmark, cwd=args.candidate, rounds=args.rounds)
        verification_result = compare(
            baseline,
            candidate,
            correctness_passed=correctness,
            minimum_improvement_percent=args.minimum_improvement,
        )
        print(json.dumps(verification_result.to_dict(), indent=2))
        return 0 if verification_result.decision == "accept" else 2
    except (
        BenchmarkError,
        ProfilingError,
        ProviderError,
        RepositoryError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
