from __future__ import annotations

import contextlib
import os
import signal
from pathlib import Path
from typing import Any, cast


def apply_limits(policy: Any) -> None:
    import resource

    resource_api = cast(Any, resource)
    resource_api.setrlimit(resource_api.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
    resource_api.setrlimit(
        resource_api.RLIMIT_NPROC, (policy.maximum_processes, policy.maximum_processes)
    )
    resource_api.setrlimit(
        resource_api.RLIMIT_FSIZE, (policy.maximum_file_bytes, policy.maximum_file_bytes)
    )
    resource_api.setrlimit(resource_api.RLIMIT_CORE, (0, 0))


def popen_platform_options(policy: Any) -> dict[str, Any]:
    return {"start_new_session": True, "preexec_fn": lambda: apply_limits(policy)}


def terminate_process_tree(process: Any) -> None:
    with contextlib.suppress(ProcessLookupError):
        os_api = cast(Any, os)
        signal_api = cast(Any, signal)
        os_api.killpg(process.pid, signal_api.SIGKILL)


def resident_memory_bytes(process_id: int) -> int:
    try:
        for line in Path(f"/proc/{process_id}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return 0
    return 0


def process_group_memory_bytes(process_group_id: int) -> int:
    total = 0
    try:
        process_directories = (path for path in Path("/proc").iterdir() if path.name.isdigit())
        for process_directory in process_directories:
            try:
                stat = (process_directory / "stat").read_text()
                fields = stat[stat.rfind(")") + 2 :].split()
                if len(fields) > 2 and int(fields[2]) == process_group_id:
                    total += resident_memory_bytes(int(process_directory.name))
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
    except (FileNotFoundError, PermissionError):
        return 0
    return total
