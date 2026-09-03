from __future__ import annotations

import os
import platform
import sys

from . import __version__


def environment_fingerprint() -> dict[str, str | int | None]:
    return {
        "tool_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": sys.platform,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
    }
