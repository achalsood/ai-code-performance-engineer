from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .evaluation import EvaluationRun


@dataclass(frozen=True)
class Regression:
    metric: str
    previous: float
    current: float
    change: float


def append_run(path: Path, run: EvaluationRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(run), sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_runs(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def detect_regressions(
    previous: dict[str, object], current: EvaluationRun, *, tolerance_percent: float = 5.0
) -> list[Regression]:
    old = previous["summary"]
    if not isinstance(old, dict):
        raise ValueError("invalid historical summary")
    regressions: list[Regression] = []
    for metric in ("acceptance_rate", "correctness_rate", "median_speedup_percent"):
        old_value = float(old[metric])
        new_value = float(getattr(current.summary, metric))
        change = new_value - old_value
        if change < -tolerance_percent:
            regressions.append(Regression(metric, old_value, new_value, change))
    return regressions
