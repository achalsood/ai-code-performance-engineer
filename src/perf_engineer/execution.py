from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from . import execution_posix, execution_windows


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


def popen_platform_options(policy: ExecutionPolicy) -> dict[str, Any]:
    if os.name == "posix":
        return execution_posix.popen_platform_options(policy)
    return execution_windows.popen_platform_options(policy)


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if os.name == "posix":
        execution_posix.terminate_process_tree(process)
    else:
        execution_windows.terminate_process_tree(process)


def _resident_memory_bytes(process_id: int) -> int:
    if os.name == "posix":
        return execution_posix.resident_memory_bytes(process_id)
    return execution_windows.resident_memory_bytes(process_id)


def _process_group_memory_bytes(process_id: int) -> int:
    if os.name == "posix":
        return execution_posix.process_group_memory_bytes(process_id)
    return execution_windows.process_group_memory_bytes(process_id)


def process_tree_memory_bytes(process_id: int) -> int:
    """Return the current resident memory of a process tree."""
    return _process_group_memory_bytes(process_id)


def _windows_descendant_process_ids(root_process_id: int) -> set[int]:
    if os.name != "nt":
        return {root_process_id}
    return execution_windows.descendant_process_ids(root_process_id)


def _process_cpu_seconds(process_id: int) -> float:
    if os.name != "nt":
        return 0.0
    return execution_windows.process_cpu_seconds(process_id)


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
                **popen_platform_options(policy),
            )
            deadline = started + policy.timeout_seconds
            stopped = threading.Event()
            monitoring_peak = _process_group_memory_bytes(process.pid)
            process_cpu_peaks: dict[int, float] = {}
            violation: str | None = None

            def sample_windows_cpu() -> None:
                if os.name != "nt":
                    return
                for process_id in _windows_descendant_process_ids(process.pid):
                    process_cpu_peaks[process_id] = max(
                        process_cpu_peaks.get(process_id, 0.0),
                        _process_cpu_seconds(process_id),
                    )

            sample_windows_cpu()

            def monitor() -> None:
                nonlocal monitoring_peak, violation
                while not stopped.wait(0.002):
                    observed = _process_group_memory_bytes(process.pid)
                    monitoring_peak = max(monitoring_peak, observed)
                    sample_windows_cpu()
                    if monitoring_peak > policy.memory_bytes:
                        violation = "memory"
                    elif time.perf_counter() >= deadline:
                        violation = "timeout"
                    else:
                        continue
                    terminate_process_tree(process)
                    return

            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            if os.name == "posix":
                os_api = cast(Any, os)
                _, status, child_usage = os_api.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
                cpu_seconds = child_usage.ru_utime + child_usage.ru_stime
                peak_memory_bytes = max(monitoring_peak, int(child_usage.ru_maxrss * 1024))
            else:
                process.wait()
                sample_windows_cpu()
                cpu_seconds = sum(process_cpu_peaks.values())
                peak_memory_bytes = max(
                    monitoring_peak, _process_group_memory_bytes(process.pid)
                )
            finished = time.perf_counter()
            stopped.set()
            monitor_thread.join()
            returncode = process.returncode
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
            cpu_seconds=cpu_seconds,
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
