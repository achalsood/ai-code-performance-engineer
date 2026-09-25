from perf_engineer.evaluation import CategorySummary, EvaluationRun, EvaluationSummary
from perf_engineer.history import Regression
from perf_engineer.reporting import markdown_report


def test_markdown_report_renders_summary_and_no_regressions() -> None:
    summary = EvaluationSummary(
        1,
        1,
        100.0,
        50.0,
        12.5,
        8.0,
        17.0,
        (CategorySummary("loops", 1, 100.0, 50.0, 12.5),),
    )
    run = EvaluationRun(1, "cross-platform", "now", (), summary)

    report = markdown_report(run, [])

    assert "# cross-platform evaluation" in report
    assert "| Correctness rate | 100.0% |" in report
    assert "| Acceptance rate | 50.0% |" in report
    assert "| loops | 1 | 100.0% | 50.0% | 12.5% |" in report
    assert "No aggregate regression exceeded the configured tolerance." in report


def test_markdown_report_renders_regressions() -> None:
    summary = EvaluationSummary(0, 0, 100.0, 50.0, 12.5, 8.0, 17.0)
    run = EvaluationRun(1, "cross-platform", "now", (), summary)
    regression = Regression("acceptance_rate", 75.0, 50.0, -25.0)

    report = markdown_report(run, [regression])

    assert "- acceptance_rate: 75.0 → 50.0 (-25.0)" in report
