import os
import sys

import pytest


def _exclude_incompatible_execution_coverage(config: pytest.Config) -> None:
    """Exclude execution.py branches that cannot run on this host platform."""
    cov_plugin = config.pluginmanager.getplugin("_cov")
    if cov_plugin is None or cov_plugin.cov_controller is None:
        return
    coverage = cov_plugin.cov_controller.cov
    excluded = list(coverage.get_option("report:exclude_lines") or [])
    platform_pattern = (
        r"^\s*if os\.name == [\"']posix[\"']:"
        if os.name != "posix"
        else r"^\s*(?:if|elif) os\.name == [\"']nt[\"']:"
    )
    coverage.set_option("report:exclude_lines", [*excluded, platform_pattern])


def pytest_sessionstart(session: pytest.Session) -> None:
    _exclude_incompatible_execution_coverage(session.config)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    windows_only = pytest.mark.skip(reason="Windows-only test")
    posix_only = pytest.mark.skip(reason="POSIX-only test")

    for item in items:
        if "windows" in item.keywords and sys.platform != "win32":
            item.add_marker(windows_only)
        if "posix" in item.keywords and os.name != "posix":
            item.add_marker(posix_only)


def pytest_ignore_collect(collection_path) -> bool:
    name = collection_path.name
    if name.endswith("_windows.py") and sys.platform != "win32":
        return True
    return name.endswith("_posix.py") and os.name != "posix"
