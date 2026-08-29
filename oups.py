#! /usr/bin/env python3
import re
from collections.abc import Generator
from io import BytesIO
from subprocess import run

spaces = re.compile(rb"\s+")


class Log:
    commit = None
    author = None
    date = None
    message = None


def branches() -> tuple[str, list[str]]:
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
    l = None
    for line in txt.split(b"\n"):
        if line == b"":
            continue
        if line.startswith(b"commit"):
            if l is not None:
                l.message = l.message.getvalue()
                yield l
            l = Log()
        if l.commit is None:
            l.commit = int(line.strip().split(b" ")[1], 16)
        elif l.author is None:
            l.author = line.strip().split(b" ", maxsplit=1)[1]
        elif l.date is None:
            l.date = spaces.split(line.strip(), maxsplit=1)[1]
        elif l.message is None:
            l.message = BytesIO()
        elif line.startswith(b"    ") or line == b"":
            l.message.write(line)
        else:
            l.message = l.message.getvalue()
            yield l
            l = Log()
    yield l


def logs(branch: str) -> Generator[Log, None, None]:
    return parse_log(
        run(
            ["git", "log", "--decorate=short", branch], check=True, capture_output=True
        ).stdout
    )


if __name__ == "__main__":
    main, bb = branches()
    for b in bb:
        print(b)
        for l in logs(b):
            print("\t", l.commit)
