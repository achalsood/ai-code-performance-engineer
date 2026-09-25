from dataclasses import replace
from pathlib import Path

import pytest

from perf_engineer.evaluation import EvaluationRun, EvaluationSummary
from perf_engineer.history import append_run, detect_regressions, read_runs


def test_detects_material_metric_drop() -> None:
    summary = EvaluationSummary(2, 2, 100.0, 100.0, 30.0, 20.0, 40.0)
    previous = {
        "summary": {
            "correctness_rate": 100,
            "acceptance_rate": 100,
            "median_speedup_percent": 30,
        }
    }
    current = EvaluationRun(1, "suite", "now", (), replace(summary, acceptance_rate=80.0))
    regressions = detect_regressions(previous, current, tolerance_percent=5.0)
    assert [item.metric for item in regressions] == ["acceptance_rate"]



def test_history_round_trip_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "history" / "runs.jsonl"
    assert read_runs(path) == []

    summary = EvaluationSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    run = EvaluationRun(1, "suite", "now", (), summary)
    append_run(path, run)

    runs = read_runs(path)
    assert len(runs) == 1
    assert runs[0]["suite_name"] == "suite"
    assert runs[0]["summary"]["total_cases"] == 0


def test_rejects_invalid_historical_summary() -> None:
    summary = EvaluationSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    current = EvaluationRun(1, "suite", "now", (), summary)

    with pytest.raises(ValueError, match="invalid historical summary"):
        detect_regressions({"summary": "invalid"}, current)
