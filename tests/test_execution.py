import sys
from pathlib import Path

import pytest

from perf_engineer.execution import (
    ExecutionError,
    ExecutionPolicy,
    LocalProcessRunner,
    _popen_platform_options,
    _process_group_memory_bytes,
    _resident_memory_bytes,
    sanitized_environment,
)


def test_sanitized_environment_drops_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    assert "OPENAI_API_KEY" not in sanitized_environment()
    assert sanitized_environment()["PYTHONHASHSEED"] == "0"


def test_runner_enforces_wall_timeout(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError):
        LocalProcessRunner().run(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            cwd=tmp_path,
            policy=ExecutionPolicy(timeout_seconds=0.05),
        )


def test_runner_reports_per_process_memory(tmp_path: Path) -> None:
    result = LocalProcessRunner().run(
        [
            sys.executable,
            "-c",
            "import time; data = bytearray(32_000_000); time.sleep(0.15)",
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.peak_memory_bytes >= 16_000_000


@pytest.mark.windows
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
    assert result.peak_memory_bytes < 100_000_000


@pytest.mark.posix
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


@pytest.mark.posix
def test_posix_platform_options_create_isolated_session() -> None:
    options = _popen_platform_options(ExecutionPolicy())
    assert options["start_new_session"] is True
    assert callable(options["preexec_fn"])


@pytest.mark.posix
def test_posix_resident_memory_reads_current_process() -> None:
    assert _resident_memory_bytes(__import__("os").getpid()) > 0


@pytest.mark.posix
def test_posix_process_group_memory_reads_current_group() -> None:
    import os

    assert _process_group_memory_bytes(os.getpgrp()) > 0


def test_execution_policy_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="all execution limits must be positive"):
        ExecutionPolicy(timeout_seconds=0)
