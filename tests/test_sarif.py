from perf_engineer.models import Finding
from perf_engineer.sarif import findings_to_sarif


def test_sarif_contains_rules_and_locations() -> None:
    report = findings_to_sarif(
        [Finding("PERF001", "src/work.py", 12, "high", "Slow loop", "Use an index")]
    )
    run = report["runs"][0]
    assert run["tool"]["driver"]["rules"][0]["id"] == "PERF001"
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"] == {
        "startLine": 12
    }
