from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _load_callable(module_name: str, callable_name: str) -> Any:
    module = importlib.import_module(module_name)
    target: Any = module
    for part in callable_name.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{module_name}:{callable_name} is not callable")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--callable", dest="callable_name", required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(Path.cwd()))
    target = _load_callable(args.module, args.callable_name)

    for line in sys.stdin:
        request = json.loads(line)
        operation = request["operation"]
        if operation == "stop":
            return 0
        if operation != "measure":
            raise ValueError(f"unsupported worker operation: {operation}")
        repetitions = int(request.get("repetitions", 1))
        started = time.perf_counter()
        cpu_started = time.process_time()
        try:
            with open(Path(os.devnull), "w", encoding="utf-8") as sink:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    for _ in range(repetitions):
                        target()
            payload = {
                "wall_seconds": time.perf_counter() - started,
                "cpu_seconds": time.process_time() - cpu_started,
            }
        except Exception as exc:
            payload = {
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=5),
            }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
