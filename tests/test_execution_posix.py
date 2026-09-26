import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from perf_engineer.execution import (
    ExecutionError,
    ExecutionPolicy,
    LocalProcessRunner,
    _popen_platform_options,
    _process_group_memory_bytes,
    _resident_memory_bytes,
)


def test_posix_runner_reports_cpu_and_success(tmp_path: Path) -> None:
    result = LocalProcessRunner().run(
        [sys.executable, "-c", "sum(i * i for i in range(200_000))"],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.returncode == 0
    assert result.wall_seconds > 0
    assert result.cpu_seconds > 0
    assert result.peak_memory_bytes > 0
    assert result.stderr == ""


def test_posix_platform_options_create_isolated_session() -> None:
    options = _popen_platform_options(ExecutionPolicy())
    assert options["start_new_session"] is True
    assert callable(options["preexec_fn"])


def test_posix_resident_memory_reads_current_process() -> None:
    assert _resident_memory_bytes(os.getpid()) > 0


def test_posix_process_group_memory_reads_current_group() -> None:
    assert _process_group_memory_bytes(os.getpgrp()) > 0


def test_posix_resident_memory_returns_zero_for_missing_process() -> None:
    assert _resident_memory_bytes(999_999_999) == 0


def test_posix_runner_reports_failed_command_stderr(tmp_path: Path) -> None:
    result = LocalProcessRunner().run(
        [
            sys.executable,
            "-c",
            "import sys; print('expected failure', file=sys.stderr); raise SystemExit(7)",
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.returncode == 7
    assert "expected failure" in result.stderr


def test_posix_runner_enforces_timeout_and_kills_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished.txt"
    child = (
        "import pathlib, time; time.sleep(1); "
        f"pathlib.Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(10)"
    )
    with pytest.raises(ExecutionError, match="timed out"):
        LocalProcessRunner().run(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            policy=ExecutionPolicy(timeout_seconds=0.1),
        )
    time.sleep(1.1)
    assert not marker.exists()


def test_posix_process_group_memory_includes_child_process(tmp_path: Path) -> None:
    child = "data = bytearray(20_000_000); import time; time.sleep(2)"
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(2)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", parent],
        cwd=tmp_path,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 1.0
        observed = 0
        while time.monotonic() < deadline:
            observed = max(observed, _process_group_memory_bytes(process.pid))
            if observed >= 20_000_000:
                break
            time.sleep(0.01)
        assert observed >= 20_000_000
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_posix_runner_enforces_process_tree_memory_limit(tmp_path: Path) -> None:
    child = "data = bytearray(20_000_000); import time; time.sleep(2)"
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(2)"
    )
    with pytest.raises(ExecutionError, match="memory limit"):
        LocalProcessRunner().run(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            policy=ExecutionPolicy(memory_bytes=15_000_000),
        )
