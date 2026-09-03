from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .analyzer import analyze_path
from .audit import AuditLogger
from .benchmark import BenchmarkError, run_benchmark
from .evaluation import evaluate_corpus
from .execution import DockerRunner, ExecutionPolicy, LocalProcessRunner
from .experiments import run_experiment, save_record
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
    optimize_parser.add_argument("--maximum-candidates", type=int, default=3)
    optimize_parser.add_argument("--minimum-improvement", type=float, default=5.0)
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
            payload = {**record.to_dict(), "record_path": str(destination)}
            print(json.dumps(payload, indent=2))
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
                runner=runner,
                policy=policy,
                audit_logger=AuditLogger(args.audit_log),
            )
            record_path = save_optimization(optimization, args.output)
            patch_path = export_winning_patch(optimization, args.output_patch)
            payload = {
                **optimization.to_dict(),
                "record_path": str(record_path),
                "winner_patch_path": str(patch_path) if patch_path else None,
            }
            print(json.dumps(payload, indent=2))
            return 0 if optimization.winner_id else 2

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
            payload = {
                **evaluation.to_dict(),
                "regressions": [asdict(item) for item in regressions],
                "report_path": str(args.report),
            }
            print(json.dumps(payload, indent=2))
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
