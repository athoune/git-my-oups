#! /usr/bin/env python3
import argparse
import datetime as dt
import os
import re
import sys
from collections.abc import Generator
from fnmatch import fnmatch
from io import BytesIO
from subprocess import CalledProcessError, CompletedProcess, run

spaces = re.compile(rb"\s+")

DATE_FORMAT = r"%a %b %d %H:%M:%S %Y %z"
TEST_BRANCH_NAME = "___test-rebase"


class Git:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def __call__(self, *args, error=False) -> CompletedProcess[bytes]:
        if os.getenv("VERBOSE") == "1":
            print("git", *args)
        try:
            proc = run(
                ["git"] + list(args),
                check=True,
                capture_output=True,
                cwd=self.repo_path,
                env={"LC_ALL": "C"},
            )
        except CalledProcessError as e:
            if error:
                sys.stderr.write(
                    f'Error running "git {" ".join(args)}\n\n{e.stderr.decode()}"\n'
                )
            raise
        return proc

    def last_commit_date(self, branch: str) -> dt.datetime:
        return dt.datetime.strptime(
            self("log", "-1", r"--pretty=format:%ci", branch).stdout.strip().decode(),
            r"%Y-%m-%d %H:%M:%S %z",
        ).astimezone()

    @property
    def config(self) -> dict[str, str | bool]:
        proc = self("config", "list")
        c = {}
        for line in proc.stdout.decode().split("\n"):
            if "=" not in line:
                continue
            k, v = line.split("=", maxsplit=1)
            k, v = k.strip(), v.strip()
            if v.lower() in ("true", "false"):
                c[k] = v.lower() == "true"
            else:
                c[k] = v
        return c


class Log:
    commit: bytes
    author: str
    author_date: dt.datetime
    committer: str
    commit_date: dt.datetime
    message: str
    merge: bytes

    def __init__(self):
        self.__buffer = BytesIO()
        self.commit = b""

    def write_message(self, line: bytes):
        self.__buffer.write(line)

    def read_message(self) -> str:
        return self.__buffer.getvalue().decode()


class Branch:
    name: str
    current: bool
    _logs: list[Log]
    project: "Project"

    def __init__(self, name: str, project: "Project"):
        self.name = name
        self.project = project
        self._logs = []

    def logs(self) -> list[Log]:
        if not self._logs:
            self._logs = list(logs(self.project.git, self.name))
        return self._logs

    def local_checkout(self) -> str:
        if not self.name.startswith("remotes/"):
            raise ValueError("Cannot checkout local branch")
        local_name = self.name.split("/", maxsplit=2)[-1]
        current = self.project.git("branch", "--show-current").stdout.decode().strip()
        _, branches = branch_all(self.project.git, merged=True, all_branches=False)
        if local_name in branches:
            self.project.git("checkout", local_name)
            self.project.git("pull", "--rebase")
        else:
            self.project.git("checkout", "--track", self.name)
        self.project.git("checkout", current)
        return local_name

    def try_to_merge_with_main(self):
        self.project.git("fetch")
        if self.name == "remotes/origin/main":
            local_name = self.name.split("/", maxsplit=2)[-1]
        else:
            local_name = self.local_checkout()
        self.project.git("merge-tree", "--write-tree", local_name, self.name)

    def last_commit_date(self) -> dt.datetime:
        return self.project.git.last_commit_date(self.name)


class Project:
    name: str
    current_branch: str
    __branches: dict[str, Branch]
    __branches_name: list[str]
    git: Git

    def __init__(self, git: Git, merged=False):  # only works in the current directory
        self.git = git
        self.current_branch, self.__branches_name = branch_all(self.git, merged)
        self.__branches = {}

    @property
    def branches(self) -> dict[str, Branch]:
        if not self.__branches:
            for name in self.__branches_name:
                self.__branches[name] = Branch(name, self)
        return self.__branches

    def fresh_branches(
        self, delta: dt.timedelta = dt.timedelta(days=30), include=None
    ) -> list[Branch]:
        now = dt.datetime.now(dt.timezone.utc)
        fresh: list[Branch] = []
        for name in self.__branches_name:
            if include is not None and not fnmatch(name, include):
                continue
            if name == "remotes/origin/HEAD":
                continue
            branch_date = self.git.last_commit_date(name)
            if now - branch_date < delta:
                fresh.append(Branch(name, self))
        return fresh

    def test_rebase_with_remote_main(self):
        for cmd in [
            ["checkout", "-b", TEST_BRANCH_NAME],
            ["rebase", "origin/main"],
            ["checkout", self.current_branch],
            ["branch", "-D", TEST_BRANCH_NAME],
        ]:
            try:
                self.git(*cmd)
            except CalledProcessError as e:
                print(e.args)
                print(e.stderr)

    def remote_main(self, include="remotes/origin/*"):
        """Try to merge every fresh remote branch with main and report conflicts."""
        for branch in self.fresh_branches(include=include):
            print("#", branch.name, end="")
            try:
                branch.try_to_merge_with_main()
            except CalledProcessError as e:
                print(
                    f""" 🔥

Error occurred while merging branch {branch.name}:"""
                )
                stdout = e.stdout.decode()
                if stdout:
                    print(f"""
STDOUT:

    {stdout}

""")
                stderr = e.stderr.decode()
                if stderr:
                    print(f"""
STDERR:

    {stderr}
""")
            else:
                print(" ✅")


def branch_all(
    git: Git | None = None,
    merged=False,
    all_branches=True,
    include: list[str] | None = None,
) -> tuple[str, list[str]]:
    if git is None:
        git = Git(os.getcwd())
    proc = git("branch", "--show-current")
    current = proc.stdout.strip().decode()

    command = ["branch"]
    if all_branches:
        command.append("--all")
    if not merged:
        command.append("--no-merged")
    proc = git(*command)
    b = []
    for line in proc.stdout.split(b"\n"):
        if line.startswith(b"* "):
            continue
        line = line.strip()
        m = re.match(rb"\S+", line)
        if m is None:
            continue
        branch_name = m.group(0).decode()
        if include is None or any(fnmatch(branch_name, i) for i in include):
            b.append(branch_name)
    return current, b


def parse_log(txt: bytes) -> Generator[Log, None, None]:
    log = Log()
    for line in txt.split(b"\n"):
        if line.startswith(b"commit"):
            if log.commit != b"":
                log.message = log.read_message()
                yield log
            log = Log()
            log.commit = line.strip().split(b" ")[1]
        elif line.startswith(b"Author:"):
            log.author = line.strip().split(b" ", maxsplit=1)[1].strip().decode()
        elif line.startswith(b"AuthorDate:"):
            log.author_date = dt.datetime.strptime(
                spaces.split(line.strip(), maxsplit=1)[1].decode(), DATE_FORMAT
            ).astimezone()
        elif line.startswith(b"Commit:"):
            log.committer = line.strip().split(b" ", maxsplit=1)[1].strip().decode()
        elif line.startswith(b"CommitDate:"):
            log.commit_date = dt.datetime.strptime(
                spaces.split(line.strip(), maxsplit=1)[1].decode(), DATE_FORMAT
            ).astimezone()
        elif line.startswith(b"Merge:"):
            log.merge = line.strip().split(b" ")[1].strip()
        elif line.startswith(b"    ") or line == b"":
            log.write_message(line)
    yield log


def logs(git: Git | None = None, branch: str = "HEAD") -> Generator[Log, None, None]:
    if git is None:
        git = Git(os.getcwd())
    return parse_log(git("log", "--format=fuller", branch).stdout)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="oups.py", description="Avoid git merge conflicts and other dramas"
    )
    parser.add_argument(
        "--path", type=str, default=os.getcwd(), help="Path to the git repository"
    )
    subparsers = parser.add_subparsers(
        title="subcommands", help="operations", dest="command", required=True
    )
    subparsers.add_parser(
        "remote-main",
        help="Test if all active remote branches can be rebased with remote main",
    )

    args = parser.parse_args(argv)
    git = Git(args.path)
    project = Project(git)

    if args.command == "remote-main":
        project.remote_main()
    else:  # unreachable: required=True makes argparse exit on missing/unknown command
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
