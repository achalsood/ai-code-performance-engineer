from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .execution import ExecutionPolicy, sanitized_environment


class CallableBenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True)
class PythonCallableTarget:
    module: str
    callable_name: str


@dataclass(frozen=True)
class CallableMeasurement:
    wall_seconds: float
    cpu_seconds: float


class PythonCallableSession:
    """Persistent isolated interpreter for an explicitly selected Python callable."""

    def __init__(self, target: PythonCallableTarget, *, cwd: Path, policy: ExecutionPolicy) -> None:
        self._policy = policy
        worker = Path(__file__).with_name("_python_benchmark_worker.py")
        self._process = subprocess.Popen(
            [
                sys.executable,
                str(worker),
                "--module",
                target.module,
                "--callable",
                target.callable_name,
            ],
            cwd=cwd,
            env=sanitized_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def measure(self, repetitions: int = 1) -> CallableMeasurement:
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        if self._process.stdin is None or self._process.stdout is None:
            raise CallableBenchmarkError("callable benchmark worker pipes are unavailable")
        if self._process.poll() is not None:
            raise CallableBenchmarkError(self._worker_error("callable benchmark worker exited"))
        self._process.stdin.write(
            json.dumps({"operation": "measure", "repetitions": repetitions}) + "\n"
        )
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            raise CallableBenchmarkError(self._worker_error("callable benchmark worker failed"))
        try:
            payload = json.loads(line)
            return CallableMeasurement(
                wall_seconds=float(payload["wall_seconds"]),
                cpu_seconds=float(payload["cpu_seconds"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CallableBenchmarkError("callable benchmark worker returned invalid data") from exc

    def _worker_error(self, prefix: str) -> str:
        stderr = ""
        if self._process.stderr is not None and self._process.poll() is not None:
            stderr = self._process.stderr.read().strip()
        return f"{prefix}: {stderr}" if stderr else prefix

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        if self._process.stdin is not None:
            try:
                self._process.stdin.write(json.dumps({"operation": "stop"}) + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            self._process.wait(timeout=min(self._policy.timeout_seconds, 2.0))
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def __enter__(self) -> PythonCallableSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
