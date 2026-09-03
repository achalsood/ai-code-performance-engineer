from __future__ import annotations

import contextlib
import os
import resource
import signal
import subprocess
import tempfile
import threading
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


def _process_group_memory_bytes(process_group_id: int) -> int:
    total = 0
    try:
        process_directories = (path for path in Path("/proc").iterdir() if path.name.isdigit())
        for process_directory in process_directories:
            try:
                stat = (process_directory / "stat").read_text()
                fields = stat[stat.rfind(")") + 2 :].split()
                if len(fields) > 2 and int(fields[2]) == process_group_id:
                    total += _resident_memory_bytes(int(process_directory.name))
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
    except (FileNotFoundError, PermissionError):
        return 0
    return total


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
            stopped = threading.Event()
            monitoring_peak = 0
            violation: str | None = None

            def monitor() -> None:
                nonlocal monitoring_peak, violation
                while not stopped.wait(0.01):
                    observed = _process_group_memory_bytes(process.pid)
                    monitoring_peak = max(monitoring_peak, observed)
                    if monitoring_peak > policy.memory_bytes:
                        violation = "memory"
                    elif time.perf_counter() >= deadline:
                        violation = "timeout"
                    else:
                        continue
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    return

            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            _, status, child_usage = os.wait4(process.pid, 0)
            finished = time.perf_counter()
            process.returncode = os.waitstatus_to_exitcode(status)
            stopped.set()
            monitor_thread.join()
            returncode = process.returncode
            peak_memory_bytes = max(
                monitoring_peak, int(child_usage.ru_maxrss * 1024)
            )
            errors.seek(0)
            stderr = errors.read().decode("utf-8", errors="replace")[-1000:]
        if violation == "memory":
            raise ExecutionError(
                f"command exceeded memory limit of {policy.memory_bytes} bytes"
            )
        if violation == "timeout":
            raise ExecutionError(f"command timed out after {policy.timeout_seconds:g}s")
        return ExecutionResult(
            returncode=returncode,
            wall_seconds=finished - started,
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
