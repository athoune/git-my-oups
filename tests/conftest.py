"""Shared fixtures and git helpers for the oups.py functional tests.

The tests build real git repositories (a working tree plus a bare "origin"
remote) inside pytest's tmp_path and run oups.py as a subprocess, exactly
like a user would. No external dependency: only the system git binary.

A "pusher" fixture provides a second clone used to advance the remote in
ways the main repo does not know about until it fetches.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

OUPS = Path(__file__).resolve().parent.parent / "oups.py"


def git(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run a git command in cwd and fail loudly on any error."""
    full_env = os.environ.copy()
    full_env["LC_ALL"] = "C"  # deterministic git output regardless of locale
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=full_env,
        check=True,
        capture_output=True,
        text=True,
    )


def write_and_commit(
    cwd: Path, message: str, files: dict[str, str], date: str | None = None
) -> None:
    """Create files and commit them in cwd, optionally with a fixed date."""
    for name, content in files.items():
        path = cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(cwd, "add", name)
    env = None
    if date is not None:
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    git(cwd, "commit", "-q", "-m", message, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with a bare 'origin' remote and one commit on main."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", "--initial-branch=main", str(origin))
    repo_path = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(repo_path))
    git(repo_path, "config", "user.name", "Tester")
    git(repo_path, "config", "user.email", "tester@example.com")
    write_and_commit(repo_path, "initial", {"a.txt": "base\n"})
    git(repo_path, "push", "-q", "-u", "origin", "main")
    return repo_path


@pytest.fixture
def pusher(tmp_path: Path, repo: Path) -> Path:
    """A second clone of the same origin, used to advance remote branches."""
    clone = tmp_path / "pusher"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(clone))
    git(clone, "config", "user.name", "Pusher")
    git(clone, "config", "user.email", "pusher@example.com")
    return clone


def run_oups(*args: str, repo_path: Path | None = None, cwd: Path | None = None):
    """Run oups.py as a subprocess.

    With repo_path, the --path option is used. With cwd, oups.py is run from
    inside the repository (exercising the default current-directory behavior).
    """
    cmd = [sys.executable, str(OUPS)]
    if repo_path is not None:
        cmd += ["--path", str(repo_path)]
    cmd += list(args)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)