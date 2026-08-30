#! /usr/bin/env python3
import re
from collections.abc import Generator
from io import BytesIO
from subprocess import run

spaces = re.compile(rb"\s+")



class Log:
    commit: bytes
    author: str
    author_date: bytes
    commiter: str
    commit_date: bytes
    message: str
    merge: bytes

    def __init__(self):
        self._buffer = BytesIO()
        self.commit = b""


class Branch:
    name: bytes
    current: bool
    logs: list[Log]


def branch_all() -> tuple[str, list[str]]:
    cmd = run(["git", "branch", "--all"], capture_output=True, check=True)
    current = ""
    b = []
    for line in cmd.stdout.split(b"\n"):
        line = line.strip()
        if line == b"":
            continue
        if line.startswith(b"*"):
            current = line[2:].decode()
        elif line.find(b" -> ") > 0:
            continue
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
            l.author_date = spaces.split(line.strip(), maxsplit=1)[1]
        elif line.startswith(b"Commit:"):
            l.commiter = line.strip().split(b" ", maxsplit=1)[1].strip().decode()
        elif line.startswith(b"CommitDate:"):
            l.commit_date = spaces.split(line.strip(), maxsplit=1)[1]
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
    main, bb = branch_all()
    for b in bb:
        print(b)
        for l in logs(b):
            print("\t", l.commit)
