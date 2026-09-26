from __future__ import annotations

import contextlib
import subprocess
from typing import Any, cast


def popen_platform_options(policy: Any) -> dict[str, Any]:
    subprocess_api = cast(Any, subprocess)
    return {"creationflags": subprocess_api.CREATE_NEW_PROCESS_GROUP}


def terminate_process_tree(process: Any) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _memory_counters(process_id: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_ulonglong), ("WorkingSetSize", ctypes.c_ulonglong),
            ("QuotaPeakPagedPoolUsage", ctypes.c_ulonglong),
            ("QuotaPagedPoolUsage", ctypes.c_ulonglong),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_ulonglong),
            ("QuotaNonPagedPoolUsage", ctypes.c_ulonglong), ("PagefileUsage", ctypes.c_ulonglong),
            ("PeakPagefileUsage", ctypes.c_ulonglong),
        ]

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    psapi = cast(Any, ctypes).WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0400, False, process_id)
    if not handle:
        return None
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return (
            int(counters.PeakWorkingSetSize), int(counters.WorkingSetSize),
            int(counters.PagefileUsage), int(counters.PeakPagefileUsage),
        )
    finally:
        kernel32.CloseHandle(handle)


def resident_memory_bytes(process_id: int) -> int:
    counters = _memory_counters(process_id)
    return max(counters) if counters is not None else 0


def resident_working_set_bytes(process_id: int) -> int:
    counters = _memory_counters(process_id)
    return counters[1] if counters is not None else 0


def process_cpu_seconds(process_id: int) -> float:
    import ctypes
    from ctypes import wintypes

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return 0.0
    try:
        creation, exit_time, kernel, user = (
            wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        )
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user)
        ):
            return 0.0
        def value(item: wintypes.FILETIME) -> int:
            return (int(item.dwHighDateTime) << 32) | int(item.dwLowDateTime)
        return (value(kernel) + value(user)) / 10_000_000.0
    finally:
        kernel32.CloseHandle(handle)


def descendant_process_ids(root_process_id: int) -> set[int]:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
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


def process_group_memory_bytes(root_process_id: int) -> int:
    return sum(resident_working_set_bytes(pid) for pid in descendant_process_ids(root_process_id))
