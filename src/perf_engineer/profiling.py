from __future__ import annotations

import pstats
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from .execution import CommandRunner, ExecutionPolicy, LocalProcessRunner


class ProfilingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Hotspot:
    function: str
    file: str
    line: int
    calls: int
    self_seconds: float
    cumulative_seconds: float


@dataclass(frozen=True)
class ProfileResult:
    adapter: str
    command: tuple[str, ...]
    wall_seconds: float
    cpu_seconds: float
    peak_memory_bytes: int
    hotspots: tuple[Hotspot, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ProfilerAdapter(Protocol):
    def profile(
        self, command: list[str], *, cwd: Path, policy: ExecutionPolicy
    ) -> ProfileResult: ...


class ResourceProfiler:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or LocalProcessRunner()

    def profile(
        self, command: list[str], *, cwd: Path, policy: ExecutionPolicy
    ) -> ProfileResult:
        result = self.runner.run(command, cwd=cwd, policy=policy)
        if result.returncode:
            message = f"profiled command exited with {result.returncode}: {result.stderr}"
            raise ProfilingError(message)
        return ProfileResult(
            "resource",
            tuple(command),
            result.wall_seconds,
            result.cpu_seconds,
            result.peak_memory_bytes,
        )


class CProfileAdapter:
    def __init__(self, runner: CommandRunner | None = None, *, maximum_hotspots: int = 20) -> None:
        self.runner = runner or LocalProcessRunner()
        self.maximum_hotspots = maximum_hotspots

    def profile(
        self, command: list[str], *, cwd: Path, policy: ExecutionPolicy
    ) -> ProfileResult:
        if not command or "python" not in Path(command[0]).name.lower():
            raise ProfilingError("cProfile requires a Python command")
        with tempfile.NamedTemporaryFile(suffix=".prof") as profile_file:
            wrapped = [command[0], "-m", "cProfile", "-o", profile_file.name, *command[1:]]
            measured = self.runner.run(wrapped, cwd=cwd, policy=policy)
            if measured.returncode:
                raise ProfilingError(
                    f"profiled command exited with {measured.returncode}: {measured.stderr}"
                )
            statistics = pstats.Stats(profile_file.name)
            raw_statistics = cast(
                dict[tuple[str, int, str], tuple[int, int, float, float, dict[Any, Any]]],
                statistics.stats,  # type: ignore[attr-defined]
            )
            hotspots = sorted(
                (
                    Hotspot(
                        function=function_name,
                        file=filename,
                        line=line,
                        calls=values[1],
                        self_seconds=values[2],
                        cumulative_seconds=values[3],
                    )
                    for (filename, line, function_name), values in raw_statistics.items()
                ),
                key=lambda item: (-item.cumulative_seconds, -item.self_seconds, item.function),
            )[: self.maximum_hotspots]
        return ProfileResult(
            "cprofile",
            tuple(command),
            measured.wall_seconds,
            measured.cpu_seconds,
            measured.peak_memory_bytes,
            tuple(hotspots),
        )
