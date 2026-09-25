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
            "import time; data = bytearray(32_000_000); time.sleep(0.15)",
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.peak_memory_bytes >= 16_000_000


def test_execution_policy_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="all execution limits must be positive"):
        ExecutionPolicy(timeout_seconds=0)


def test_runner_captures_stderr_and_exit_code(tmp_path: Path) -> None:
    result = LocalProcessRunner().run(
        [
            sys.executable,
            "-c",
            "import sys; print('failure detail', file=sys.stderr); raise SystemExit(7)",
        ],
        cwd=tmp_path,
        policy=ExecutionPolicy(),
    )
    assert result.returncode == 7
    assert result.stderr == "failure detail\n"
    assert result.wall_seconds > 0


def test_runner_enforces_memory_limit(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError, match="exceeded memory limit"):
        LocalProcessRunner().run(
            [
                sys.executable,
                "-c",
                "import time; data = bytearray(64_000_000); time.sleep(1)",
            ],
            cwd=tmp_path,
            policy=ExecutionPolicy(memory_bytes=16_000_000, timeout_seconds=2),
        )


def test_docker_runner_builds_hardened_command(tmp_path: Path) -> None:
    from perf_engineer.execution import DockerRunner, ExecutionResult

    runner = DockerRunner("python:test")
    captured = {}

    def fake_run(command, *, cwd, policy):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["policy"] = policy
        return ExecutionResult(0, 0.1, 0.0, 1024, "")

    runner.local.run = fake_run
    policy = ExecutionPolicy(memory_bytes=123_456_789, maximum_processes=12)

    result = runner.run(["python", "workload.py"], cwd=tmp_path, policy=policy)

    assert result.returncode == 0
    command = captured["command"]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=12" in command
    assert "--memory=123456789" in command
    assert f"{tmp_path.resolve()}:/workspace:ro" in command
    assert command[-3:] == ["python:test", "python", "workload.py"]
