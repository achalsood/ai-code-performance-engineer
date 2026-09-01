from __future__ import annotations

import os
import resource
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionPolicy:
    timeout_seconds: float = 30.0
    cpu_seconds: int = 30
    memory_bytes: int = 1_073_741_824
    maximum_processes: int = 64
    maximum_file_bytes: int = 67_108_864

    def __post_init__(self) -> None:
        if min(
            self.timeout_seconds,
            self.cpu_seconds,
            self.memory_bytes,
            self.maximum_processes,
            self.maximum_file_bytes,
        ) <= 0:
            raise ValueError("all execution limits must be positive")


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    wall_seconds: float
    cpu_seconds: float
    peak_memory_bytes: int
    stderr: str


class CommandRunner(Protocol):
    def run(self, command: list[str], *, cwd: Path, policy: ExecutionPolicy) -> ExecutionResult: ...


def sanitized_environment() -> dict[str, str]:
    allowed = {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _apply_limits(policy: ExecutionPolicy) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_NPROC, (policy.maximum_processes, policy.maximum_processes))
    resource.setrlimit(
        resource.RLIMIT_FSIZE, (policy.maximum_file_bytes, policy.maximum_file_bytes)
    )
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _resident_memory_bytes(process_id: int) -> int:
    try:
        for line in Path(f"/proc/{process_id}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return 0
    return 0


class LocalProcessRunner:
    """Resource-limited runner for trusted repositories."""

    def run(self, command: list[str], *, cwd: Path, policy: ExecutionPolicy) -> ExecutionResult:
        started = time.perf_counter()
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=sanitized_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                start_new_session=True,
                preexec_fn=lambda: _apply_limits(policy),
            )
            deadline = started + policy.timeout_seconds
            peak_memory_bytes = 0
            while True:
                peak_memory_bytes = max(peak_memory_bytes, _resident_memory_bytes(process.pid))
                if peak_memory_bytes > policy.memory_bytes:
                    os.killpg(process.pid, signal.SIGKILL)
                    _, status, _ = os.wait4(process.pid, 0)
                    process.returncode = os.waitstatus_to_exitcode(status)
                    raise ExecutionError(
                        f"command exceeded memory limit of {policy.memory_bytes} bytes"
                    )
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    os.killpg(process.pid, signal.SIGKILL)
                    _, status, _ = os.wait4(process.pid, 0)
                    process.returncode = os.waitstatus_to_exitcode(status)
                    message = f"command timed out after {policy.timeout_seconds:g}s"
                    raise ExecutionError(message)
                waited_pid, status, child_usage = os.wait4(process.pid, os.WNOHANG)
                if waited_pid:
                    returncode = os.waitstatus_to_exitcode(status)
                    process.returncode = returncode
                    peak_memory_bytes = max(
                        peak_memory_bytes, int(child_usage.ru_maxrss * 1024)
                    )
                    break
                time.sleep(min(0.005, remaining))
            errors.seek(0)
            stderr = errors.read().decode("utf-8", errors="replace")[-1000:]
        return ExecutionResult(
            returncode=returncode,
            wall_seconds=time.perf_counter() - started,
            cpu_seconds=child_usage.ru_utime + child_usage.ru_stime,
            peak_memory_bytes=peak_memory_bytes,
            stderr=stderr,
        )


class DockerRunner:
    """Network-disabled, read-only container runner for untrusted repository code."""

    def __init__(self, image: str = "python:3.12-slim") -> None:
        self.image = image
        self.local = LocalProcessRunner()

    def run(self, command: list[str], *, cwd: Path, policy: ExecutionPolicy) -> ExecutionResult:
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={policy.maximum_processes}",
            f"--memory={policy.memory_bytes}",
            "--cpus=1",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "-e",
            "PYTHONHASHSEED=0",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{cwd.resolve()}:/workspace:ro",
            "-w",
            "/workspace",
            self.image,
            *command,
        ]
        return self.local.run(docker_command, cwd=cwd, policy=policy)
