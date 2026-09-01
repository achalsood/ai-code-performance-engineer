from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktrees:
    baseline: Path
    candidate: Path
    baseline_commit: str
    candidate_commit: str


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RepositoryError(f"git {' '.join(arguments)} failed: {message}")
    return completed.stdout.strip()


def resolve_commit(repository: Path, revision: str) -> str:
    return _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")


@contextlib.contextmanager
def isolated_worktrees(
    repository: Path, baseline_ref: str, candidate_ref: str
) -> Iterator[Worktrees]:
    repository = repository.resolve()
    if not (repository / ".git").exists():
        raise RepositoryError(f"not a Git repository: {repository}")
    baseline_commit = resolve_commit(repository, baseline_ref)
    candidate_commit = resolve_commit(repository, candidate_ref)

    with tempfile.TemporaryDirectory(prefix="perf-engineer-") as temporary:
        root = Path(temporary)
        baseline = root / "baseline"
        candidate = root / "candidate"
        added: list[Path] = []
        try:
            for path, commit in ((baseline, baseline_commit), (candidate, candidate_commit)):
                _git(repository, "worktree", "add", "--detach", str(path), commit)
                added.append(path)
            yield Worktrees(baseline, candidate, baseline_commit, candidate_commit)
        finally:
            for path in reversed(added):
                subprocess.run(
                    ["git", "-C", str(repository), "worktree", "remove", "--force", str(path)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=False,
                )
            subprocess.run(
                ["git", "-C", str(repository), "worktree", "prune"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
