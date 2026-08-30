#! /usr/bin/env python3
import datetime as dt
import re
from collections.abc import Generator
from io import BytesIO
from subprocess import CalledProcessError, run

spaces = re.compile(rb"\s+")

DATE_FORMAT = "%a %b %d %H:%M:%S %Y %z"
TEST_BRANCH_NAME = "___test-rebase"


class Log:
    commit: bytes
    author: str
    author_date: dt.datetime
    commiter: str
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
            ["git", "checkout", "-b", TEST_BRANCH_NAME],
            ["git", "rebase", "origin/main"],
            ["git", "checkout", self.current_branch],
            ["git", "branch", "-D", TEST_BRANCH_NAME],
        ]:
            try:
                run(cmd, check=True, capture_output=True)
            except CalledProcessError as e:
                print(e.args)
                print(e.stderr)


def branch_all(merged=False) -> tuple[str, list[str]]:
    proc = run(["git", "branch", "--show-current"], capture_output=True, check=True)
    current = proc.stdout.strip().decode()

    command = ["git", "branch", "--all"]
    if not merged:
        command.append("--no-merged")
    proc = run(command, capture_output=True, check=True)
    b = []
    for line in proc.stdout.split(b"\n"):
        line = line.strip()
        if line == b"":
            continue
        else:
            z = line.find(b" -> ")
            if z > 0:
                b.append(line.decode()[:z])
            else:
                b.append(line.decode())
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
            l.commiter = line.strip().split(b" ", maxsplit=1)[1].strip().decode()
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
    return parse_log(
        run(
            ["git", "log", "--format=fuller", branch], check=True, capture_output=True
        ).stdout
    )


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
