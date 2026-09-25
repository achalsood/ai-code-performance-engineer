import os
import sys

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    windows_only = pytest.mark.skip(reason="Windows-only test")
    posix_only = pytest.mark.skip(reason="POSIX-only test")

    for item in items:
        if "windows" in item.keywords and sys.platform != "win32":
            item.add_marker(windows_only)
        if "posix" in item.keywords and os.name != "posix":
            item.add_marker(posix_only)
