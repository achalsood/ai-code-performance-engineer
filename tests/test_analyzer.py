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


def test_ignores_membership_in_invariant_set_inside_loop(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text(
        "allowed = {'.py', '.js'}\n"
        "for path in paths:\n"
        "    if path.suffix not in allowed:\n"
        "        raise ValueError(path)\n"
    )
    assert "PERF004" not in {finding.rule_id for finding in analyze_file(source)}


def test_detects_membership_in_invariant_sequence(tmp_path: Path) -> None:
    source = tmp_path / "slow.py"
    source.write_text(
        "values = list(range(100))\n"
        "for needle in queries:\n"
        "    print(needle in values)\n"
    )
    assert "PERF004" in {finding.rule_id for finding in analyze_file(source)}


def test_ignores_membership_in_loop_bound_sequence(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text("for items in batches:\n    if needle in items:\n        print(needle)\n")
    assert "PERF004" not in {finding.rule_id for finding in analyze_file(source)}


def test_ignores_membership_in_set_built_from_comprehension(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text(
        "used_ids = {item.candidate_id for item in candidates}\n"
        "for candidate in refined:\n"
        "    if candidate.candidate_id in used_ids:\n"
        "        print(candidate)\n"
    )
    assert "PERF004" not in {finding.rule_id for finding in analyze_file(source)}


def test_ignores_membership_in_annotated_set_inside_loop(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text(
        "seen: set[tuple[str, str]] = set()\n"
        "for candidate in candidates:\n"
        "    key = (baseline_state, candidate_state)\n"
        "    if key in seen:\n"
        "        continue\n"
    )
    assert "PERF004" not in {finding.rule_id for finding in analyze_file(source)}


def test_ignores_loop_dependent_list_allocation(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text(
        "for case in cases:\n"
        "    run_correctness(list(case.test_command))\n"
    )
    assert "PERF002" not in {finding.rule_id for finding in analyze_file(source)}


def test_detects_invariant_list_allocation_inside_loop(tmp_path: Path) -> None:
    source = tmp_path / "slow.py"
    source.write_text(
        "values = tuple(range(10))\n"
        "for case in cases:\n"
        "    consume(list(values))\n"
    )
    assert "PERF002" in {finding.rule_id for finding in analyze_file(source)}
