from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from .analyzer import analyze_path
from .benchmark import BenchmarkError, run_benchmark
from .experiments import run_experiment, save_record
from .repository import RepositoryError
from .verification import compare, run_correctness


def _command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise argparse.ArgumentTypeError("command cannot be empty")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="perf-engineer")
    subparsers = parser.add_subparsers(dest="action", required=True)
    analyze = subparsers.add_parser("analyze", help="find static performance risks")
    analyze.add_argument("path", type=Path)
    analyze.add_argument("--json", action="store_true")

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "analyze":
            findings = analyze_path(args.path)
            if args.json:
                print(json.dumps([asdict(item) for item in findings], indent=2))
            else:
                for item in findings:
                    print(f"{item.path}:{item.line}: {item.severity} {item.rule_id} {item.message}")
            return 0
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
    except (BenchmarkError, RepositoryError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
