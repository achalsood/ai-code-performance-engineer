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
