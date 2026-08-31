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
    _logs: list[Log]

    def __init__(self, name):
        self.name = name
        self._logs = []

    def logs(self) -> list[Log]:
        if self._logs == []:
            self._logs = list(logs(self.name))
        return self._logs

    def local_checkout(self) -> str:
        if not self.name.startswith("remotes/"):
            raise ValueError("Cannot checkout local branch")
        local_name = self.name.split("/", maxsplit=2)[-1]
        current = git_("branch", "--show-current").stdout.decode().strip()
        _, branches = branch_all(merged=True, all=False)
        if local_name in branches:
            git_("checkout", local_name)
            git_("pull", "--rebase")
        else:
            git_("checkout", "--track", self.name)
        git_("checkout", current)
        return local_name

    def try_to_merge_with_main(self):
        git_("fetch")
        if self.name == "remotes/origin/main":
            local_name = self.name.split("/", maxsplit=2)[-1]
        else:
            local_name = self.local_checkout()
        merge_base = git_("merge-base", local_name, self.name).stdout.strip().decode()
        git_("merge-tree", merge_base, local_name, self.name)

    def last_commit_date(self) -> dt.datetime:
        return dt.datetime.strptime(
            git_("log", "-1", "--pretty=format:'%ci'", self.name)
            .stdout.strip()
            .decode(),
            DATE_FORMAT,
        ).astimezone()


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
                self.__branches[name] = Branch(name)
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
            branch_date = dt.datetime.strptime(
                git_("log", "-1", r"--pretty=format:%ci", name).stdout.strip().decode(),
                r"%Y-%m-%d %H:%M:%S %z",
            ).astimezone()
            if now - branch_date < delta:
                fresh.append(Branch(name))
        return fresh

    def test_rebase_with_remote_main(self):
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


def branch_all(
    merged=False, all=True, include: list[str] | None = None
) -> tuple[str, list[str]]:
    proc = git_("branch", "--show-current")
    current = proc.stdout.strip().decode()

    command = ["branch"]
    if all:
        command.append("--all")
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
        if include is None or any(fnmatch(branch_name, i) for i in include):
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
    # project.test_rebase()
    for branch in project.fresh_branches(include="remotes/origin/*"):
        print("Branch name:", branch.name)
        try:
            branch.try_to_merge_with_main()
        except Exception as e:
            print(f"Error occurred while merging branch {branch.name}: {e}")
