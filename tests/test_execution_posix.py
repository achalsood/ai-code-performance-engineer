import os
import sys
from pathlib import Path

from perf_engineer.execution import (
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
