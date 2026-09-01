from perf_engineer.cli import main


def test_empty_analysis_returns_success(tmp_path, capsys) -> None:
    (tmp_path / "clean.py").write_text("def add(a, b):\n    return a + b\n")
    assert main(["analyze", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""

