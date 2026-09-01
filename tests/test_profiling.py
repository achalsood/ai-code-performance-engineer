import sys
from pathlib import Path

from perf_engineer.execution import ExecutionPolicy
from perf_engineer.profiling import CProfileAdapter, ResourceProfiler


def test_resource_profiler_returns_normalized_metrics(tmp_path: Path) -> None:
    result = ResourceProfiler().profile(
        [sys.executable, "-c", "sum(range(1000))"],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.adapter == "resource"
    assert result.wall_seconds > 0


def test_cprofile_returns_ranked_hotspots(tmp_path: Path) -> None:
    script = tmp_path / "work.py"
    script.write_text("def work():\n    return sum(range(1000))\nwork()\n")
    result = CProfileAdapter(maximum_hotspots=5).profile(
        [sys.executable, "work.py"], cwd=tmp_path, policy=ExecutionPolicy()
    )
    assert result.adapter == "cprofile"
    assert result.hotspots
    assert any(item.function == "work" for item in result.hotspots)
