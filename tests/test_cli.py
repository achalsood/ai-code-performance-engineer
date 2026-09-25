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
