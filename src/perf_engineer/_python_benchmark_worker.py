from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import runpy
import sys
import time
from pathlib import Path


def _run_script(script: Path, arguments: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = [str(script), *arguments]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = old_argv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--warmups", type=int, required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--target-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    script = args.script.resolve()
    if not script.is_file():
        raise SystemExit(f"benchmark script does not exist: {script}")

    for _ in range(args.warmups):
        _run_script(script, args.arguments)

    started = time.perf_counter()
    _run_script(script, args.arguments)
    probe = time.perf_counter() - started
    repetitions = max(1, min(10_000, int(args.target_seconds / max(probe, 1e-9) + 0.999999)))

    samples: list[float] = []
    cpu_samples: list[float] = []
    for _ in range(args.rounds):
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        for _ in range(repetitions):
            _run_script(script, args.arguments)
        cpu_samples.append(time.process_time() - cpu_started)
        samples.append(time.perf_counter() - wall_started)

    payload = {
        "probe_seconds": probe,
        "repetitions": repetitions,
        "samples_seconds": samples,
        "cpu_seconds": cpu_samples,
        "pid": os.getpid(),
    }
    args.output.write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
