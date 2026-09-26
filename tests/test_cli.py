import json

import pytest

from perf_engineer.cli import main


def test_empty_analysis_returns_success(tmp_path, capsys) -> None:
    (tmp_path / "clean.py").write_text("def add(a, b):\n    return a + b\n")
    assert main(["analyze", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""



def test_arena_parser_accepts_command_provider(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    args = build_parser().parse_args(
        [
            "arena",
            "--corpus",
            str(tmp_path / "corpus.json"),
            "--provider-command",
            "python provider.py",
        ]
    )
    assert args.action == "arena"
    assert args.maximum_candidates == 3


def test_fix_parser_uses_local_deterministic_engine(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    args = build_parser().parse_args(
        [
            "fix",
            "--repository",
            str(tmp_path),
            "--benchmark",
            "python benchmark.py",
            "--test",
            "pytest",
        ]
    )
    assert args.action == "fix"
    assert args.maximum_candidates == 3
    assert args.minimum_improvement == 5.0
    assert args.calibration_summary is False


def test_fix_parser_accepts_explicit_callable_benchmark(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    args = build_parser().parse_args(
        [
            "fix",
            "--repository",
            str(tmp_path),
            "--benchmark-callable",
            "workload:benchmark",
            "--test",
            "pytest",
        ]
    )
    assert args.benchmark is None
    assert args.benchmark_callable.module == "workload"
    assert args.benchmark_callable.callable_name == "benchmark"


def test_optimize_parser_accepts_explicit_callable_benchmark(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    args = build_parser().parse_args(
        [
            "optimize",
            "--repository",
            str(tmp_path),
            "--provider-command",
            "python provider.py",
            "--benchmark-callable",
            "package.workload:benchmark",
            "--test",
            "pytest",
        ]
    )
    assert args.benchmark is None
    assert args.benchmark_callable.module == "package.workload"
    assert args.benchmark_callable.callable_name == "benchmark"


def test_fix_parser_rejects_command_and_callable_benchmarks(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "fix",
                "--repository",
                str(tmp_path),
                "--benchmark",
                "python workload.py",
                "--benchmark-callable",
                "workload:benchmark",
                "--test",
                "pytest",
            ]
        )


@pytest.mark.parametrize("value", ["workload", ":benchmark", "workload:"])
def test_callable_parser_rejects_invalid_target(value) -> None:
    import argparse

    from perf_engineer.cli import _python_callable

    with pytest.raises(argparse.ArgumentTypeError, match="MODULE:CALLABLE"):
        _python_callable(value)


def test_calibrate_parser_accepts_adaptive_measurement_options(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    args = build_parser().parse_args(
        [
            "calibrate",
            "--baseline",
            str(tmp_path / "baseline"),
            "--candidate",
            str(tmp_path / "candidate"),
            "--benchmark",
            "python benchmark.py",
            "--rounds",
            "5",
            "--maximum-rounds",
            "9",
        ]
    )
    assert args.action == "calibrate"
    assert args.rounds == 5
    assert args.maximum_rounds == 9
    assert args.warmups == 2


def test_fix_parser_accepts_calibration_summary(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    args = build_parser().parse_args(
        [
            "fix",
            "--repository",
            str(tmp_path),
            "--benchmark",
            "python workload.py",
            "--test",
            "pytest",
            "--calibration-summary",
        ]
    )
    assert args.action == "fix"
    assert args.calibration_summary is True


def test_fix_parser_accepts_fixture_directory(tmp_path) -> None:
    from perf_engineer.cli import build_parser

    fixture = tmp_path / "fixture"
    args = build_parser().parse_args(
        [
            "fix",
            "--fixture",
            str(fixture),
            "--benchmark",
            "python workload.py",
            "--test",
            "python test_correctness.py",
        ]
    )
    assert args.action == "fix"
    assert args.fixture == fixture


def test_command_rejects_empty_value() -> None:
    import argparse

    from perf_engineer.cli import _command

    with pytest.raises(argparse.ArgumentTypeError, match="cannot be empty"):
        _command("   ")


def test_analyze_json_writes_output_and_honors_fail_threshold(tmp_path, monkeypatch) -> None:
    from perf_engineer.models import Finding

    destination = tmp_path / "reports" / "findings.json"
    finding = Finding(
        path="slow.py",
        line=3,
        rule_id="PERF999",
        severity="high",
        message="slow path",
        suggestion="make it faster",
    )
    monkeypatch.setattr("perf_engineer.cli.analyze_path", lambda path: [finding])

    assert main(
        [
            "analyze", str(tmp_path), "--format", "json", "--output", str(destination),
            "--fail-on", "high",
        ]
    ) == 4
    assert '"rule_id": "PERF999"' in destination.read_text(encoding="utf-8")


def test_analyze_sarif_prints_serialized_report(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("perf_engineer.cli.analyze_path", lambda path: [])
    monkeypatch.setattr(
        "perf_engineer.cli.findings_to_sarif",
        lambda findings: {"version": "2.1.0", "runs": []},
    )

    assert main(["analyze", str(tmp_path), "--format", "sarif"]) == 0
    assert '"version": "2.1.0"' in capsys.readouterr().out


def test_benchmark_command_serializes_result(tmp_path, monkeypatch, capsys) -> None:
    from perf_engineer.models import BenchmarkResult

    measured = BenchmarkResult(("python",), (1.0,) * 3, 1.0, 1.0, 0.0, 1.0, 1.0)
    monkeypatch.setattr("perf_engineer.cli.run_benchmark", lambda *args, **kwargs: measured)

    assert main(["benchmark", "python bench.py", "--cwd", str(tmp_path)]) == 0
    assert '"median_seconds": 1.0' in capsys.readouterr().out


def test_cli_reports_benchmark_error(tmp_path, monkeypatch, capsys) -> None:
    from perf_engineer.benchmark import BenchmarkError

    def fail(*args, **kwargs):
        raise BenchmarkError("measurement failed")

    monkeypatch.setattr("perf_engineer.cli.run_benchmark", fail)

    assert main(["benchmark", "python bench.py", "--cwd", str(tmp_path)]) == 1
    assert "error: measurement failed" in capsys.readouterr().err


def test_calibrate_command_serializes_adaptive_metadata(tmp_path, monkeypatch, capsys) -> None:
    from perf_engineer.models import BenchmarkResult

    measured = BenchmarkResult(
        ("python",), (1.0,) * 3, 1.0, 1.0, 0.0, 1.0, 1.0,
        calibration_probe_seconds=0.05,
        repetitions_per_sample=2,
        measurement_rounds=3,
        total_measurement_seconds=1.2,
    )
    monkeypatch.setattr(
        "perf_engineer.cli.run_adaptive_paired_benchmarks",
        lambda *args, **kwargs: (measured, measured),
    )

    assert main([
        "calibrate",
        "--baseline", str(tmp_path / "baseline"),
        "--candidate", str(tmp_path / "candidate"),
        "--benchmark", "python bench.py",
        "--rounds", "3",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["calibration"]["probe_seconds"] == 0.05
    assert payload["calibration"]["repetitions_per_sample"] == 2
    assert payload["calibration"]["measurement_rounds"] == 3


def test_profile_command_writes_output(tmp_path, monkeypatch, capsys) -> None:
    class FakeProfile:
        def to_dict(self):
            return {"adapter": "resource", "wall_seconds": 1.0}

    class FakeProfiler:
        def profile(self, *args, **kwargs):
            return FakeProfile()

    destination = tmp_path / "reports" / "profile.json"
    monkeypatch.setattr("perf_engineer.cli.ResourceProfiler", lambda: FakeProfiler())

    assert main([
        "profile", "python workload.py",
        "--cwd", str(tmp_path),
        "--output", str(destination),
    ]) == 0
    assert json.loads(destination.read_text())["wall_seconds"] == 1.0
    assert json.loads(capsys.readouterr().out)["adapter"] == "resource"


def test_profile_command_selects_cprofile_adapter(tmp_path, monkeypatch) -> None:
    selected = {}

    class FakeProfile:
        def to_dict(self):
            return {"adapter": "cprofile"}

    class FakeProfiler:
        def profile(self, *args, **kwargs):
            selected["called"] = True
            return FakeProfile()

    monkeypatch.setattr("perf_engineer.cli.CProfileAdapter", lambda: FakeProfiler())

    assert main([
        "profile", "python workload.py",
        "--cwd", str(tmp_path),
        "--adapter", "cprofile",
    ]) == 0
    assert selected["called"] is True


def test_experiment_command_saves_record_and_returns_accept(tmp_path, monkeypatch, capsys) -> None:
    from types import SimpleNamespace

    class FakeRecord:
        result = SimpleNamespace(decision="accept")

        def to_dict(self):
            return {"experiment_id": "exp-1", "result": {"decision": "accept"}}

    monkeypatch.setattr("perf_engineer.cli.run_experiment", lambda **kwargs: FakeRecord())
    destination = tmp_path / "records" / "exp-1.json"
    monkeypatch.setattr("perf_engineer.cli.save_record", lambda record, output: destination)

    assert main([
        "experiment",
        "--repository", str(tmp_path),
        "--baseline-ref", "main~1",
        "--candidate-ref", "main",
        "--benchmark", "python bench.py",
        "--test", "pytest",
        "--output", str(tmp_path / "records"),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["experiment_id"] == "exp-1"
    assert payload["record_path"] == str(destination)


def test_verify_command_returns_rejection_status(tmp_path, monkeypatch, capsys) -> None:
    from types import SimpleNamespace

    from perf_engineer.models import BenchmarkResult

    measured = BenchmarkResult(("python",), (1.0,) * 3, 1.0, 1.0, 0.0, 1.0, 1.0)
    verification = SimpleNamespace(
        decision="reject",
        to_dict=lambda: {"decision": "reject", "reason": "not faster"},
    )
    monkeypatch.setattr("perf_engineer.cli.run_correctness", lambda *args, **kwargs: True)
    monkeypatch.setattr("perf_engineer.cli.run_benchmark", lambda *args, **kwargs: measured)
    monkeypatch.setattr("perf_engineer.cli.compare", lambda *args, **kwargs: verification)

    assert main([
        "verify",
        "--baseline", str(tmp_path / "baseline"),
        "--candidate", str(tmp_path / "candidate"),
        "--benchmark", "python bench.py",
        "--test", "pytest",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["decision"] == "reject"



@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["benchmark", "python bench.py", "--rounds", "0"], "greater than zero"),
        (
            [
                "fix",
                "--benchmark", "python bench.py",
                "--test", "pytest",
                "--maximum-candidates", "0",
            ],
            "greater than zero",
        ),
        (
            [
                "optimize",
                "--provider-command", "python provider.py",
                "--benchmark", "python bench.py",
                "--test", "pytest",
                "--minimum-improvement", "-1",
            ],
            "zero or greater",
        ),
    ],
)
def test_cli_rejects_invalid_numeric_arguments(arguments, message, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(arguments)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
