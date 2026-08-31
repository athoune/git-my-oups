#! /usr/bin/env python3
import datetime as dt
import os
import re
from collections.abc import Generator
from fnmatch import fnmatch
from io import BytesIO
from subprocess import CalledProcessError, CompletedProcess, run

spaces = re.compile(rb"\s+")

DATE_FORMAT = r"%a %b %d %H:%M:%S %Y %z"
TEST_BRANCH_NAME = "___test-rebase"


def git_(*args) -> CompletedProcess[bytes]:
    if os.getenv("VERBOSE") == "1":
        print("git", *args)
    try:
        proc = run(["git"] + list(args), check=True, capture_output=True)
    except CalledProcessError as e:
        print(f'Error running "git {" ".join(args)}\n\n{e.stderr.decode()}"\n')
        raise
    return proc


class Log:
    commit: bytes
    author: str
    author_date: dt.datetime
    committer: str
    commit_date: dt.datetime
    message: str
    merge: bytes

    def __init__(self):
        self._buffer = BytesIO()
        self.commit = b""


class Branch:
    name: str
    current: bool
    logs: list[Log]

    def __init__(self, name):
        self.name = name
        self.logs = list(logs(name))

    def local_checkout(self):
        if not self.name.startswith("remotes/"):
            raise ValueError("Cannot checkout local branch")
        local_name = self.name.split("/", maxsplit=2)[-1]
        if local_name in git_("branch").stdout.decode():
            current = git_("branch", "--show-current").stdout.decode().strip()
            git_("checkout", local_name)
            git_("git", "pull", "--rebase")
            git_("git", "checkout", current)
        git_("checkout", self.name)


class Project:
    name: str
    current_branch: str
    __branches: dict[str, Branch]
    __branches_name: list[str]

    def __init__(self, merged=False):  # only works in the current directory
        self.current_branch, self.__branches_name = branch_all(merged)
        self.__branches = {}

    @property
    def branches(self) -> dict[str, Branch]:
        if self.__branches == {}:
            for name in self.__branches_name:
                self.branches[name] = Branch(name)
        return self.__branches

    def fresh_branches(
        self, delta: dt.timedelta = dt.timedelta(days=30)
    ) -> list[Branch]:
        now = dt.datetime.now(dt.timezone.utc)
        return [
            branch
            for branch in self.branches.values()
            if now - branch.logs[0].commit_date < delta
        ]

    def test_rebase(self):
        for cmd in [
            ["checkout", "-b", TEST_BRANCH_NAME],
            ["rebase", "origin/main"],
            ["checkout", self.current_branch],
            ["branch", "-D", TEST_BRANCH_NAME],
        ]:
            try:
                git_(*cmd)
            except CalledProcessError as e:
                print(e.args)
                print(e.stderr)


def branch_all(merged=False, include: list[str] | None = None) -> tuple[str, list[str]]:
    if include is None:
        include: list[str] = []
    proc = git_("branch", "--show-current")
    current = proc.stdout.strip().decode()

    command = ["branch", "--all"]
    if not merged:
        command.append("--no-merged")
    proc = git_(*command)
    b = []
    for line in proc.stdout.split(b"\n"):
        if line.startswith(b"* "):
            continue
        line = line.strip()
        m = re.match(rb"\S+", line)
        if m is None:
            continue
        branch_name = m.group(0).decode()
        ok = False
        for i in include:
            if fnmatch(branch_name, i):
                ok = True
                break
        if ok or include == []:
            b.append(branch_name)
    return current, b


def parse_log(txt: bytes) -> Generator[Log, None, None]:
    l = Log()
    for line in txt.split(b"\n"):
        if line.startswith(b"commit"):
            if l.commit != b"":
                l.message = l._buffer.getvalue().decode()
                yield l
            l = Log()
            l.commit = line.strip().split(b" ")[1]
        elif line.startswith(b"Author:"):
            l.author = line.strip().split(b" ", maxsplit=1)[1].strip().decode()
        elif line.startswith(b"AuthorDate:"):
            l.author_date = dt.datetime.strptime(
                spaces.split(line.strip(), maxsplit=1)[1].decode(), DATE_FORMAT
            ).astimezone()
        elif line.startswith(b"Commit:"):
            l.committer = line.strip().split(b" ", maxsplit=1)[1].strip().decode()
        elif line.startswith(b"CommitDate:"):
            l.commit_date = dt.datetime.strptime(
                spaces.split(line.strip(), maxsplit=1)[1].decode(), DATE_FORMAT
            ).astimezone()
        elif line.startswith(b"Merge:"):
            l.merge = line.strip().split(b" ")[1].strip()
        elif line.startswith(b"    ") or line == b"":
            l._buffer.write(line)
    yield l


def logs(branch: str) -> Generator[Log, None, None]:
    return parse_log(git_("log", "--format=fuller", branch).stdout)


if __name__ == "__main__":
    project = Project()
    """
    main, bb = branch_all()
    for branch in project.branches.values():
        print(branch.name)
        for log in branch.logs[:10]:
            print("\t", log.commit)
    print("\n\n\n")
    for branch in project.fresh_branches():
        print(branch.name, branch.logs[0].commit_date)
    """
    project.test_rebase()
