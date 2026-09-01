from dataclasses import replace

from perf_engineer.evaluation import EvaluationRun, EvaluationSummary
from perf_engineer.history import detect_regressions


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
