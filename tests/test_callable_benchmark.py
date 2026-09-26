from pathlib import Path

from perf_engineer.callable_benchmark import PythonCallableSession, PythonCallableTarget
from perf_engineer.execution import ExecutionPolicy


def test_callable_session_reuses_one_interpreter(tmp_path: Path) -> None:
    (tmp_path / "workload.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "def benchmark():\n"
        "    with Path('pids.txt').open('a', encoding='utf-8') as stream:\n"
        "        stream.write(f'{os.getpid()}\\n')\n"
        "    sum(i * i for i in range(1000))\n",
        encoding="utf-8",
    )

    with PythonCallableSession(
        PythonCallableTarget("workload", "benchmark"),
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    ) as session:
        first = session.measure(3)
        second = session.measure(2)

    pids = (tmp_path / "pids.txt").read_text(encoding="utf-8").splitlines()
    assert len(pids) == 5
    assert len(set(pids)) == 1
    assert first.wall_seconds > 0.0
    assert second.wall_seconds > 0.0


def test_callable_sessions_keep_baseline_and_candidate_isolated(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    source = "def benchmark():\n    return sum(range(1000))\n"
    (baseline / "workload.py").write_text(source, encoding="utf-8")
    (candidate / "workload.py").write_text(source, encoding="utf-8")
    target = PythonCallableTarget("workload", "benchmark")

    with (
        PythonCallableSession(target, cwd=baseline, policy=ExecutionPolicy()) as before,
        PythonCallableSession(target, cwd=candidate, policy=ExecutionPolicy()) as after,
    ):
        assert before.measure().wall_seconds > 0.0
        assert after.measure().wall_seconds > 0.0
        assert before._process.pid != after._process.pid
