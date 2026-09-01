from __future__ import annotations

from .evaluation import EvaluationRun
from .history import Regression


def markdown_report(run: EvaluationRun, regressions: list[Regression]) -> str:
    summary = run.summary
    lines = [
        f"# {run.suite_name} evaluation",
        "",
        f"Generated: {run.created_at}",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Cases | {summary.total_cases} |",
        f"| Correctness rate | {summary.correctness_rate:.1f}% |",
        f"| Acceptance rate | {summary.acceptance_rate:.1f}% |",
        f"| Median speedup | {summary.median_speedup_percent:.1f}% |",
        f"| 95% bootstrap CI | {summary.speedup_ci95_low:.1f}%–{summary.speedup_ci95_high:.1f}% |",
        "",
        "## Cases",
        "",
        "| Case | Decision | Speedup | 95% CI | Correct |",
        "|---|---|---:|---:|---:|",
    ]
    for result in run.results:
        verification = result.verification
        lines.append(
            f"| {result.case.case_id} | {verification.decision.value} | "
            f"{verification.speedup_percent:.1f}% | "
            f"{verification.speedup_ci95_low:.1f}%–{verification.speedup_ci95_high:.1f}% | "
            f"{'yes' if verification.correctness_passed else 'no'} |"
        )
    lines.extend(["", "## Regression check", ""])
    if regressions:
        for regression in regressions:
            lines.append(
                f"- {regression.metric}: {regression.previous:.1f} → "
                f"{regression.current:.1f} ({regression.change:.1f})"
            )
    else:
        lines.append("No aggregate regression exceeded the configured tolerance.")
    lines.append("")
    return "\n".join(lines)
