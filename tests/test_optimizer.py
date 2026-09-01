import subprocess
import sys
from pathlib import Path

from perf_engineer.optimizer import export_winning_patch, optimize, save_optimization
from perf_engineer.providers import OptimizationCandidate, OptimizationRequest


class FixedProvider:
    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        assert request.language == "python"
        patch = """diff --git a/workload.py b/workload.py
--- a/workload.py
+++ b/workload.py
@@ -1,2 +1,2 @@
 import time
-time.sleep(0.04)
+time.sleep(0.001)
"""
        return [OptimizationCandidate("fast", "Reduce wait", "Removes idle time", patch)]


def test_ranks_verified_candidate_and_cleans_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"], check=True
    )
    (repository / "workload.py").write_text("import time\ntime.sleep(0.04)\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)

    result = optimize(
        repository=repository,
        baseline_ref="HEAD",
        provider=FixedProvider(),
        benchmark_command=[sys.executable, "workload.py"],
        test_command=[sys.executable, "-m", "py_compile", "workload.py"],
        rounds=3,
    )

    assert result.winner_id == "fast"
    assert result.evaluations[0].status == "accept"
    record = save_optimization(result, tmp_path / "records")
    patch = export_winning_patch(result, tmp_path / "winner.patch")
    assert record.exists()
    assert patch and "time.sleep(0.001)" in patch.read_text()
    worktrees = subprocess.check_output(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"], text=True
    )
    assert worktrees.count("worktree ") == 1
