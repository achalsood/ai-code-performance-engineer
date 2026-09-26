import sys
from pathlib import Path

from perf_engineer.execution import ExecutionPolicy, LocalProcessRunner


def test_windows_tree_memory_does_not_sum_historical_process_peaks(tmp_path: Path) -> None:
    result = LocalProcessRunner().run(
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys, time; "
                "[subprocess.run([sys.executable, '-c', "
                "'data=bytearray(20_000_000)']) for _ in range(4)]; "
                "time.sleep(0.05)"
            ),
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    # Four children allocate 20 MB sequentially. A historical-peak bug would
    # accumulate roughly 80 MB of child allocations on top of interpreter
    # overhead. Allow platform-dependent Python overhead while keeping the
    # assertion well below that accumulated footprint.
    assert result.peak_memory_bytes < 180_000_000



def test_windows_runner_reports_process_tree_cpu_time(tmp_path: Path) -> None:
    result = LocalProcessRunner().run(
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys; "
                "subprocess.run([sys.executable, '-c', "
                "'sum(i*i for i in range(2_000_000))'], check=True); "
                "sum(i*i for i in range(2_000_000))"
            ),
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )

    assert result.cpu_seconds > 0.0
    assert result.cpu_seconds <= result.wall_seconds * 4
