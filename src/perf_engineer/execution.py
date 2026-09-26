from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast


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


def _apply_limits(policy: ExecutionPolicy) -> None:  # pragma: no cover - POSIX only
    import resource

    resource_api = cast(Any, resource)
    resource_api.setrlimit(
        resource_api.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds)
    )
    resource_api.setrlimit(
        resource_api.RLIMIT_NPROC,
        (policy.maximum_processes, policy.maximum_processes),
    )
    resource_api.setrlimit(
        resource_api.RLIMIT_FSIZE,
        (policy.maximum_file_bytes, policy.maximum_file_bytes),
    )
    resource_api.setrlimit(
        resource_api.RLIMIT_CORE, (0, 0)
    )


def _popen_platform_options(policy: ExecutionPolicy) -> dict[str, Any]:
    if os.name == "posix":  # pragma: no cover - platform-specific
        return {"start_new_session": True, "preexec_fn": lambda: _apply_limits(policy)}
    subprocess_api = cast(Any, subprocess)
    return {"creationflags": subprocess_api.CREATE_NEW_PROCESS_GROUP}


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":  # pragma: no cover - platform-specific
        import signal

        with contextlib.suppress(ProcessLookupError):
            os_api = cast(Any, os)
            signal_api = cast(Any, signal)
            os_api.killpg(process.pid, signal_api.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _resident_memory_bytes(process_id: int) -> int:
    if os.name == "nt":  # pragma: no cover - platform-specific
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_ulonglong),
                ("WorkingSetSize", ctypes.c_ulonglong),
                ("QuotaPeakPagedPoolUsage", ctypes.c_ulonglong),
                ("QuotaPagedPoolUsage", ctypes.c_ulonglong),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_ulonglong),
                ("QuotaNonPagedPoolUsage", ctypes.c_ulonglong),
                ("PagefileUsage", ctypes.c_ulonglong),
                ("PeakPagefileUsage", ctypes.c_ulonglong),
            ]

        kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
        psapi = cast(Any, ctypes).WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        # PROCESS_QUERY_INFORMATION is required by GetProcessMemoryInfo on
        # supported Windows versions. PROCESS_VM_READ is not needed here.
        handle = kernel32.OpenProcess(0x0400, False, process_id)
        if not handle:
            return 0
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                return 0
            return int(
                max(
                    counters.PeakWorkingSetSize,
                    counters.PeakPagefileUsage,
                    counters.WorkingSetSize,
                    counters.PagefileUsage,
                )
            )
        finally:
            kernel32.CloseHandle(handle)
    if os.name == "posix":  # pragma: no cover - platform-specific
        try:
            for line in Path(f"/proc/{process_id}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            return 0
    return 0


def _process_cpu_seconds(process_id: int) -> float:
    """Return user + kernel CPU time consumed by a Windows process."""
    if os.name != "nt":  # pragma: no cover - platform-specific
        return 0.0

    import ctypes
    from ctypes import wintypes

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL

    # PROCESS_QUERY_LIMITED_INFORMATION works without VM-read privileges and is
    # sufficient for GetProcessTimes on supported Windows versions.
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return 0.0
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return 0.0

        def filetime_value(value: wintypes.FILETIME) -> int:
            return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

        # FILETIME durations are expressed in 100-nanosecond units.
        return (filetime_value(kernel) + filetime_value(user)) / 10_000_000.0
    finally:
        kernel32.CloseHandle(handle)


def _resident_working_set_bytes(process_id: int) -> int:
    """Return current resident memory for a Windows process."""
    if os.name != "nt":  # pragma: no cover - platform-specific
        return _resident_memory_bytes(process_id)

    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_ulonglong),
            ("WorkingSetSize", ctypes.c_ulonglong),
            ("QuotaPeakPagedPoolUsage", ctypes.c_ulonglong),
            ("QuotaPagedPoolUsage", ctypes.c_ulonglong),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_ulonglong),
            ("QuotaNonPagedPoolUsage", ctypes.c_ulonglong),
            ("PagefileUsage", ctypes.c_ulonglong),
            ("PeakPagefileUsage", ctypes.c_ulonglong),
        ]

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    psapi = cast(Any, ctypes).WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0400, False, process_id)
    if not handle:
        return 0
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _windows_descendant_process_ids(root_process_id: int) -> set[int]:
    """Return the live Windows process tree rooted at *root_process_id*."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return {root_process_id}
    try:
        children: dict[int, list[int]] = {}
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                children.setdefault(int(entry.th32ParentProcessID), []).append(
                    int(entry.th32ProcessID)
                )
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)

    process_ids = {root_process_id}
    pending = [root_process_id]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child not in process_ids:
                process_ids.add(child)
                pending.append(child)
    return process_ids


def _process_group_memory_bytes(process_group_id: int) -> int:
    if os.name == "nt":  # pragma: no cover - platform-specific
        # Do not sum each process' historical peak: those peaks may have
        # occurred at different times and can massively overstate concurrent
        # tree memory. Sample the live working set for the whole tree instead.
        return sum(
            _resident_working_set_bytes(process_id)
            for process_id in _windows_descendant_process_ids(process_group_id)
        )
    if os.name == "posix":  # pragma: no cover - platform-specific
        total = 0
        try:
            process_directories = (
                path for path in Path("/proc").iterdir() if path.name.isdigit()
            )
            for process_directory in process_directories:
                try:
                    stat = (process_directory / "stat").read_text()
                    fields = stat[stat.rfind(")") + 2 :].split()
                    if len(fields) > 2 and int(fields[2]) == process_group_id:
                        total += _resident_memory_bytes(int(process_directory.name))
                except (
                    FileNotFoundError,
                    PermissionError,
                    ProcessLookupError,
                    ValueError,
                ):
                    continue
        except (FileNotFoundError, PermissionError):
            return 0
        return total
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
                **_popen_platform_options(policy),
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
                    _terminate_process_tree(process)
                    return

            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            if os.name == "posix":  # pragma: no cover - platform-specific
                os_api = cast(Any, os)
                _, status, child_usage = os_api.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
                cpu_seconds = child_usage.ru_utime + child_usage.ru_stime
                peak_memory_bytes = max(
                    monitoring_peak, int(child_usage.ru_maxrss * 1024)
                )
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
