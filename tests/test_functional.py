"""Functional end-to-end tests for oups.py.

Each test builds a real git repository with a bare "origin" remote in a
temporary directory, then runs `python oups.py --path <repo> remote-main`
as a subprocess and asserts on its output.

Note: oups.py exits 0 when every branch merges cleanly, 1 when at least one
conflict is found (the remote-main handler catches per-branch conflicts
itself), and 2 on CLI errors (argparse). The conflict signal is the "🔥"
marker followed by "Error occurred while merging branch ..." on stdout; the
details are surfaced in a "STDOUT:" / "STDERR:" block (merge-tree
--write-tree prints its conflict info on stdout, pull --rebase on stderr).
"""

import pytest

from conftest import git, run_oups, write_and_commit


def test_clean_feature_branch(repo):
    """A feature branch that merges cleanly is reported with ✅ and exit 0."""
    git(repo, "checkout", "-q", "-b", "feature")
    write_and_commit(repo, "feature work", {"b.txt": "feature\n"})
    git(repo, "push", "-q", "-u", "origin", "feature")
    git(repo, "checkout", "-q", "main")
    # main advances on a disjoint file: the merge stays clean
    write_and_commit(repo, "main advance", {"main.txt": "main\n"})
    git(repo, "push", "-q", "origin", "main")

    proc = run_oups("remote-main", repo_path=repo)

    assert proc.returncode == 0
    assert "# remotes/origin/feature ✅" in proc.stdout
    assert "Error occurred" not in proc.stdout


def test_conflicting_feature_branch(repo, pusher):
    """A local unpushed commit conflicting with the remote is reported with 🔥."""
    git(repo, "checkout", "-q", "-b", "feature")
    write_and_commit(repo, "feature change", {"a.txt": "feature\n"})
    git(repo, "push", "-q", "-u", "origin", "feature")
    # local work diverging on the same line, never pushed
    write_and_commit(repo, "local change", {"a.txt": "local\n"})
    git(repo, "checkout", "-q", "main")

    # someone else pushes to the same feature branch on the remote
    git(pusher, "fetch", "-q")
    git(pusher, "checkout", "-q", "-b", "feature", "origin/feature")
    write_and_commit(pusher, "pushed change", {"a.txt": "pushed\n"})
    git(pusher, "push", "-q", "origin", "feature")

    proc = run_oups("remote-main", repo_path=repo)

    assert proc.returncode == 1
    assert "# remotes/origin/feature 🔥" in proc.stdout
    assert "Error occurred while merging branch remotes/origin/feature" in proc.stdout
    # pull --rebase reports the rebase failure on stderr
    assert "STDERR:" in proc.stdout


def test_fresh_branches_filters_stale_branches(repo):
    """Branches whose last commit predates the freshness delta are skipped."""
    git(repo, "checkout", "-q", "-b", "fresh")
    write_and_commit(repo, "fresh work", {"f.txt": "x\n"})
    git(repo, "push", "-q", "-u", "origin", "fresh")

    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "stale")
    write_and_commit(repo, "stale work", {"s.txt": "y\n"}, date="2020-01-01T00:00:00+0000")
    git(repo, "push", "-q", "-u", "origin", "stale")
    git(repo, "checkout", "-q", "main")

    proc = run_oups("remote-main", repo_path=repo)

    assert proc.returncode == 0
    assert "# remotes/origin/fresh ✅" in proc.stdout
    assert "stale" not in proc.stdout


def test_merged_branch_is_not_reported(repo):
    """A branch already merged into main is not listed at all."""
    git(repo, "checkout", "-q", "-b", "merged-feature")
    write_and_commit(repo, "feature work", {"m.txt": "x\n"})
    git(repo, "push", "-q", "-u", "origin", "merged-feature")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "merged-feature", "-m", "merge feature")
    git(repo, "push", "-q", "origin", "main")

    proc = run_oups("remote-main", repo_path=repo)

    assert proc.returncode == 0
    assert "merged-feature" not in proc.stdout


def test_remote_only_branch_is_checked_out(repo, pusher):
    """A branch that only exists on the remote is locally tracked by oups."""
    git(pusher, "checkout", "-q", "-b", "remote-only", "origin/main")
    write_and_commit(pusher, "remote work", {"r.txt": "x\n"})
    git(pusher, "push", "-q", "-u", "origin", "remote-only")
    git(repo, "fetch", "-q")  # the repo must know about the new remote branch

    proc = run_oups("remote-main", repo_path=repo)

    assert proc.returncode == 0
    assert "# remotes/origin/remote-only ✅" in proc.stdout
    # local_checkout() created the local tracking branch
    assert "remote-only" in git(repo, "branch").stdout


def test_default_path_is_current_directory(repo):
    """Without --path, oups.py operates on the repository it is run from."""
    git(repo, "checkout", "-q", "-b", "feature")
    write_and_commit(repo, "feature work", {"b.txt": "x\n"})
    git(repo, "push", "-q", "-u", "origin", "feature")
    git(repo, "checkout", "-q", "main")

    proc = run_oups("remote-main", cwd=repo)

    assert proc.returncode == 0
    assert "# remotes/origin/feature ✅" in proc.stdout


def test_stale_local_main_conflicts_with_origin_main(repo, pusher):
    """Stale local main vs advanced origin/main with divergent edits → 🔥."""
    # local work on main, never pushed
    write_and_commit(repo, "local main work", {"a.txt": "local main\n"})
    # someone else pushes conflicting work to main on the remote
    git(pusher, "checkout", "-q", "main")
    write_and_commit(pusher, "pushed main work", {"a.txt": "pushed main\n"})
    git(pusher, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q")  # so origin/main is listed by the branch scan

    proc = run_oups("remote-main", repo_path=repo)

    assert proc.returncode == 1
    assert "# remotes/origin/main 🔥" in proc.stdout
    assert "Error occurred while merging branch remotes/origin/main" in proc.stdout
    # merge-tree --write-tree prints its conflict info on stdout (stderr is empty)
    assert "STDOUT:" in proc.stdout
    assert "CONFLICT" in proc.stdout