from pathlib import Path

from perf_engineer.analyzer import analyze_javascript_file, analyze_path


def test_detects_array_scan_in_javascript_loop(tmp_path: Path) -> None:
    source = tmp_path / "slow.js"
    source.write_text("for (const query of queries) { items.includes(query); }\n")
    assert "PERF102" in {item.rule_id for item in analyze_javascript_file(source)}


def test_detects_nested_typescript_loop(tmp_path: Path) -> None:
    source = tmp_path / "slow.ts"
    source.write_text(
        "const values: number[] = [];\n"
        "for (const left of values) { for (const right of values) { console.log(left, right); } }\n"
    )
    assert "PERF101" in {item.rule_id for item in analyze_path(tmp_path)}


def test_ignores_calls_outside_loops(tmp_path: Path) -> None:
    source = tmp_path / "clean.ts"
    source.write_text("const found = values.includes(1);\n")
    assert analyze_javascript_file(source) == []
