from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


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
    import resource

    resource.setrlimit(  # type: ignore[attr-defined]
        resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds)  # type: ignore[attr-defined]
    )
    resource.setrlimit(  # type: ignore[attr-defined]
        resource.RLIMIT_NPROC,  # type: ignore[attr-defined]
        (policy.maximum_processes, policy.maximum_processes),
    )
    resource.setrlimit(  # type: ignore[attr-defined]
        resource.RLIMIT_FSIZE,  # type: ignore[attr-defined]
        (policy.maximum_file_bytes, policy.maximum_file_bytes),
    )
    resource.setrlimit(  # type: ignore[attr-defined]
        resource.RLIMIT_CORE, (0, 0)  # type: ignore[attr-defined]
    )


def _popen_platform_options(policy: ExecutionPolicy) -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True, "preexec_fn": lambda: _apply_limits(policy)}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        import signal

        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
        return
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _resident_memory_bytes(process_id: int) -> int:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, process_id)
        if not handle:
            return 0
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                return 0
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    try:
        for line in Path(f"/proc/{process_id}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return 0
    return 0


def _process_group_memory_bytes(process_group_id: int) -> int:
    if os.name == "nt":
        return _resident_memory_bytes(process_group_id)
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
                **_popen_platform_options(policy),
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
                    _terminate_process_tree(process)
                    return

            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            if os.name == "posix":
                _, status, child_usage = os.wait4(process.pid, 0)  # type: ignore[attr-defined]
                process.returncode = os.waitstatus_to_exitcode(status)
                cpu_seconds = child_usage.ru_utime + child_usage.ru_stime
                peak_memory_bytes = max(
                    monitoring_peak, int(child_usage.ru_maxrss * 1024)
                )
            else:
                process.wait()
                cpu_seconds = 0.0
                peak_memory_bytes = monitoring_peak
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
