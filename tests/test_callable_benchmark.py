import os
import sys
import time
from pathlib import Path

import pytest

from perf_engineer.callable_benchmark import (
    CallableBenchmarkError,
    PythonCallableSession,
    PythonCallableTarget,
    run_paired_callable_benchmarks,
)
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
    assert first.peak_memory_bytes > 0
    assert second.peak_memory_bytes > 0


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



def test_paired_callable_benchmark_detects_speedup(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (baseline / "workload.py").write_text(
        "def benchmark():\n"
        "    total = 0\n"
        "    for i in range(20000):\n"
        "        total += i * i\n"
        "    return total\n",
        encoding="utf-8",
    )
    (candidate / "workload.py").write_text(
        "def benchmark():\n"
        "    return sum(i * i for i in range(2000))\n",
        encoding="utf-8",
    )

    before, after = run_paired_callable_benchmarks(
        PythonCallableTarget("workload", "benchmark"),
        baseline_cwd=baseline,
        candidate_cwd=candidate,
        minimum_rounds=3,
        maximum_rounds=5,
        warmups=1,
        target_sample_seconds=0.01,
    )

    assert before.measurement_rounds is not None
    assert 3 <= before.measurement_rounds <= 5
    assert after.measurement_rounds == before.measurement_rounds
    assert before.repetitions_per_sample > 1
    assert after.repetitions_per_sample == before.repetitions_per_sample
    assert after.median_seconds < before.median_seconds
    assert before.peak_memory_bytes > 0
    assert after.peak_memory_bytes > 0



def test_callable_session_times_out_stalled_work(tmp_path: Path) -> None:
    (tmp_path / "workload.py").write_text(
        "import time\n"
        "def benchmark():\n"
        "    time.sleep(1.0)\n",
        encoding="utf-8",
    )

    with PythonCallableSession(
        PythonCallableTarget("workload", "benchmark"),
        cwd=tmp_path,
        policy=ExecutionPolicy(timeout_seconds=0.05),
    ) as session, pytest.raises(CallableBenchmarkError, match="timed out"):
        session.measure()


def test_paired_callable_keeps_sampling_when_evidence_is_ambiguous(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    source = (
        "def benchmark():\n"
        "    total = 0\n"
        "    for i in range(5000):\n"
        "        total += i\n"
        "    return total\n"
    )
    (baseline / "workload.py").write_text(source, encoding="utf-8")
    (candidate / "workload.py").write_text(source, encoding="utf-8")

    before, after = run_paired_callable_benchmarks(
        PythonCallableTarget("workload", "benchmark"),
        baseline_cwd=baseline,
        candidate_cwd=candidate,
        minimum_rounds=3,
        maximum_rounds=5,
        warmups=1,
        target_sample_seconds=0.005,
    )

    assert 3 <= before.measurement_rounds <= 5
    assert after.measurement_rounds == before.measurement_rounds



def test_callable_session_enforces_memory_limit(tmp_path: Path) -> None:
    (tmp_path / "workload.py").write_text(
        "def benchmark():\n"
        "    data = bytearray(20_000_000)\n"
        "    return len(data)\n",
        encoding="utf-8",
    )

    with PythonCallableSession(
        PythonCallableTarget("workload", "benchmark"),
        cwd=tmp_path,
        policy=ExecutionPolicy(memory_bytes=5_000_000),
    ) as session, pytest.raises(CallableBenchmarkError, match="memory limit"):
        session.measure()


def test_callable_output_does_not_corrupt_worker_protocol(tmp_path: Path) -> None:
    (tmp_path / "workload.py").write_text(
        "import sys\n"
        "def benchmark():\n"
        "    print('target stdout')\n"
        "    print('target stderr', file=sys.stderr)\n"
        "    return 1\n",
        encoding="utf-8",
    )

    with PythonCallableSession(
        PythonCallableTarget("workload", "benchmark"),
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    ) as session:
        measurement = session.measure(2)

    assert measurement.wall_seconds > 0.0


def test_callable_exception_is_reported_as_target_failure(tmp_path: Path) -> None:
    (tmp_path / "workload.py").write_text(
        "def benchmark():\n"
        "    raise RuntimeError('benchmark exploded')\n",
        encoding="utf-8",
    )

    with PythonCallableSession(
        PythonCallableTarget("workload", "benchmark"),
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    ) as session, pytest.raises(
        CallableBenchmarkError,
        match="callable benchmark target failed: RuntimeError: benchmark exploded",
    ):
        session.measure()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_callable_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
    (tmp_path / "workload.py").write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "def benchmark():\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "    Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )

    with PythonCallableSession(
        PythonCallableTarget("workload", "benchmark"),
        cwd=tmp_path,
        policy=ExecutionPolicy(timeout_seconds=0.1),
    ) as session, pytest.raises(CallableBenchmarkError, match="timed out"):
        session.measure()

    child_pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        status_path = Path(f"/proc/{child_pid}/status")
        try:
            status = status_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            break
        if "\\nState:\\tZ" in status:
            break
        time.sleep(0.01)
    else:
        pytest.fail("descendant process remained alive after callable timeout")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"warmups": -1}, "warmups cannot be negative"),
        ({"target_sample_seconds": 0.0}, "target sample seconds must be positive"),
        ({"target_sample_seconds": -0.1}, "target sample seconds must be positive"),
    ],
)
def test_callable_benchmark_rejects_invalid_sampling_inputs(
    tmp_path: Path,
    kwargs: dict[str, int | float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_paired_callable_benchmarks(
            PythonCallableTarget("workload", "benchmark"),
            baseline_cwd=tmp_path,
            candidate_cwd=tmp_path,
            **kwargs,
        )
