import subprocess
from pathlib import Path

import pytest

from perf_engineer.repository import RepositoryError, isolated_worktrees


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def make_repository(path: Path) -> tuple[str, str]:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    source = path / "value.txt"
    source.write_text("before")
    git(path, "add", ".")
    git(path, "commit", "-qm", "before")
    baseline = git(path, "rev-parse", "HEAD")
    source.write_text("after")
    git(path, "commit", "-qam", "after")
    return baseline, git(path, "rev-parse", "HEAD")


def test_creates_and_cleans_isolated_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    baseline, candidate = make_repository(repository)
    with isolated_worktrees(repository, baseline, candidate) as trees:
        assert (trees.baseline / "value.txt").read_text() == "before"
        assert (trees.candidate / "value.txt").read_text() == "after"
        temporary_root = trees.baseline.parent
    assert not temporary_root.exists()
    assert len(git(repository, "worktree", "list", "--porcelain").split("worktree ")) == 2


def test_rejects_non_repository(tmp_path: Path) -> None:
    with pytest.raises(RepositoryError), isolated_worktrees(tmp_path, "HEAD", "HEAD"):
        pass
