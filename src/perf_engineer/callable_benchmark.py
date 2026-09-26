from __future__ import annotations

import json
import statistics
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .benchmark import _bootstrap_median_interval
from .execution import ExecutionPolicy, process_tree_memory_bytes, sanitized_environment
from .models import BenchmarkResult


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
    peak_memory_bytes: int


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
        memory_peak = process_tree_memory_bytes(self._process.pid)
        self._process.stdin.write(
            json.dumps({"operation": "measure", "repetitions": repetitions}) + "\n"
        )
        self._process.stdin.flush()
        line_holder: list[str] = []

        def read_response() -> None:
            if self._process.stdout is not None:
                line_holder.append(self._process.stdout.readline())

        reader = threading.Thread(target=read_response, daemon=True)
        reader.start()
        deadline = __import__("time").perf_counter() + self._policy.timeout_seconds
        while reader.is_alive() and __import__("time").perf_counter() < deadline:
            memory_peak = max(memory_peak, process_tree_memory_bytes(self._process.pid))
            if memory_peak > self._policy.memory_bytes:
                self._process.kill()
                self._process.wait()
                raise CallableBenchmarkError(
                    f"callable benchmark worker exceeded memory limit of "
                    f"{self._policy.memory_bytes} bytes"
                )
            reader.join(0.002)
        if reader.is_alive():
            self._process.kill()
            self._process.wait()
            raise CallableBenchmarkError("callable benchmark worker timed out")
        line = line_holder[0] if line_holder else ""
        if not line:
            raise CallableBenchmarkError(self._worker_error("callable benchmark worker failed"))
        try:
            payload = json.loads(line)
            return CallableMeasurement(
                wall_seconds=float(payload["wall_seconds"]),
                cpu_seconds=float(payload["cpu_seconds"]),
                peak_memory_bytes=memory_peak,
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



def run_paired_callable_benchmarks(
    target: PythonCallableTarget,
    *,
    baseline_cwd: Path,
    candidate_cwd: Path,
    minimum_rounds: int = 7,
    maximum_rounds: int = 21,
    warmups: int = 2,
    target_sample_seconds: float = 0.1,
    policy: ExecutionPolicy | None = None,
) -> tuple[BenchmarkResult, BenchmarkResult]:
    """Measure an explicit callable in isolated persistent AB/BA workers."""
    if minimum_rounds < 3 or maximum_rounds < minimum_rounds:
        raise ValueError("callable rounds require 3 <= minimum_rounds <= maximum_rounds")
    selected_policy = policy or ExecutionPolicy()
    with (
        PythonCallableSession(target, cwd=baseline_cwd, policy=selected_policy) as baseline,
        PythonCallableSession(target, cwd=candidate_cwd, policy=selected_policy) as candidate,
    ):
        sessions = {"baseline": baseline, "candidate": candidate}
        for _ in range(warmups):
            baseline.measure()
            candidate.measure()

        probe = baseline.measure()
        repetitions = max(
            1,
            min(
                10_000,
                int(target_sample_seconds / max(probe.wall_seconds, 1e-9) + 0.999999),
            ),
        )
        wall: dict[str, list[float]] = {"baseline": [], "candidate": []}
        cpu: dict[str, list[float]] = {"baseline": [], "candidate": []}
        memory: dict[str, list[int]] = {"baseline": [], "candidate": []}

        for round_index in range(maximum_rounds):
            order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
            for name in order:
                measured = sessions[name].measure(repetitions)
                wall[name].append(measured.wall_seconds)
                cpu[name].append(measured.cpu_seconds)
                memory[name].append(measured.peak_memory_bytes)

            count = round_index + 1
            if count < minimum_rounds:
                continue
            effects = [
                (before - after) / before * 100 if before else 0.0
                for before, after in zip(wall["baseline"], wall["candidate"], strict=True)
            ]
            center = statistics.median(effects)
            mad = statistics.median(abs(effect - center) for effect in effects)
            confidence_low, _ = _bootstrap_median_interval(effects)
            if mad <= 1.5 and confidence_low > 0.0:
                break

    def summarize(name: str) -> BenchmarkResult:
        samples = wall[name]
        return BenchmarkResult(
            command=(f"python-callable:{target.module}:{target.callable_name}",),
            samples_seconds=tuple(samples),
            median_seconds=statistics.median(samples),
            mean_seconds=statistics.fmean(samples),
            stdev_seconds=statistics.stdev(samples),
            min_seconds=min(samples),
            max_seconds=max(samples),
            cpu_mean_seconds=statistics.fmean(cpu[name]),
            peak_memory_bytes=max(memory[name]),
            calibration_probe_seconds=probe.wall_seconds,
            repetitions_per_sample=repetitions,
            measurement_rounds=len(samples),
            total_measurement_seconds=sum(samples),
        )

    return summarize("baseline"), summarize("candidate")
