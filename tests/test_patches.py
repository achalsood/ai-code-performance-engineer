import subprocess
from pathlib import Path

import pytest

from perf_engineer.patches import PatchValidationError, apply_patch, validate_patch

VALID_PATCH = """diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1 +1 @@
-value = 1
+value = 2
"""


def test_accepts_safe_python_patch() -> None:
    assert validate_patch(VALID_PATCH) == ("example.py",)


@pytest.mark.parametrize(
    "header",
    [
        "diff --git a/../secret.py b/../secret.py",
        "diff --git a/config.yml b/config.yml",
        "diff --git a/old.py b/new.py",
    ],
)
def test_rejects_unsafe_paths(header: str) -> None:
    with pytest.raises(PatchValidationError):
        validate_patch(f"{header}\n--- a/x\n+++ b/x\n")


def test_applies_checked_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "example.py").write_text("value = 1\n")
    assert apply_patch(tmp_path, VALID_PATCH) == ("example.py",)
    assert (tmp_path / "example.py").read_text() == "value = 2\n"
