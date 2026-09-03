from pathlib import Path

from perf_engineer.analyzer import analyze_file, discover_files


def test_detects_linear_scan_in_loop(tmp_path: Path) -> None:
    source = tmp_path / "slow.py"
    source.write_text("for item in items:\n    positions.append(items.index(item))\n")
    findings = analyze_file(source)
    assert [finding.rule_id for finding in findings] == ["PERF003"]


def test_detects_nested_loop(tmp_path: Path) -> None:
    source = tmp_path / "slow.py"
    source.write_text("for left in items:\n    for right in items:\n        print(left, right)\n")
    assert "PERF001" in {finding.rule_id for finding in analyze_file(source)}


def test_discovery_prunes_dependency_directories(tmp_path: Path) -> None:
    source = tmp_path / "src" / "work.py"
    dependency = tmp_path / "node_modules" / "ignored.js"
    source.parent.mkdir()
    dependency.parent.mkdir()
    source.write_text("pass\n")
    dependency.write_text("for (;;) {}\n")
    assert discover_files(tmp_path) == [source]
