import sys
from pathlib import Path

import pytest

from perf_engineer.execution import (
    ExecutionError,
    ExecutionPolicy,
    LocalProcessRunner,
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
            "import time; data = bytearray(8_000_000); time.sleep(0.05)",
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.peak_memory_bytes >= 8_000_000
