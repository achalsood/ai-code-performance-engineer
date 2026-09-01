from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath


class PatchValidationError(ValueError):
    pass


DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)
ALLOWED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def validate_patch(patch: str, *, maximum_bytes: int = 100_000) -> tuple[str, ...]:
    if not patch.strip() or len(patch.encode("utf-8")) > maximum_bytes:
        raise PatchValidationError("patch is empty or exceeds the size limit")
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise PatchValidationError("binary patches are not allowed")
    headers = DIFF_HEADER.findall(patch)
    if not headers:
        raise PatchValidationError("patch has no valid diff headers")
    paths: list[str] = []
    for old, new in headers:
        if old != new:
            raise PatchValidationError("file creation, deletion, and rename are not allowed")
        path = PurePosixPath(old)
        if path.is_absolute() or ".." in path.parts or path.suffix not in ALLOWED_SUFFIXES:
            raise PatchValidationError(f"unsafe or unsupported patch path: {old}")
        paths.append(str(path))
    return tuple(paths)


def apply_patch(worktree: Path, patch: str) -> tuple[str, ...]:
    paths = validate_patch(patch)
    for path in paths:
        if not (worktree / path).is_file():
            raise PatchValidationError(f"patch target does not exist: {path}")
    command = ["git", "-C", str(worktree), "apply", "--whitespace=error-all", "-"]
    checked = subprocess.run(
        command[:4] + ["--check", "-"], input=patch, text=True, capture_output=True
    )
    if checked.returncode:
        raise PatchValidationError(f"patch does not apply cleanly: {checked.stderr.strip()}")
    applied = subprocess.run(command, input=patch, text=True, capture_output=True)
    if applied.returncode:
        raise PatchValidationError(f"patch application failed: {applied.stderr.strip()}")
    return paths
