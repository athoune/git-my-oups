#! /usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Generator, Mapping
from fnmatch import fnmatch
from io import BytesIO
from subprocess import CalledProcessError, CompletedProcess, run
from typing import cast
from urllib.error import HTTPError

spaces = re.compile(rb"\s+")

DATE_FORMAT = r"%a %b %d %H:%M:%S %Y %z"
TEST_BRANCH_NAME = "___test-rebase"


class ParsingException(Exception):
    pass


class GitError(CalledProcessError):
    pass


class TooManyPullRequest(Exception):
    pass


class NotInRemoteBranch(Exception):
    pass


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
                env={**os.environ, "LC_ALL": "C"},
            )
        except CalledProcessError as e:
            if error:
                sys.stderr.write(
                    f'Error running "git {" ".join(args)}\n\n{e.stderr.decode()}"\n'
                )
            raise GitError(e.returncode, e.cmd, e.output, e.stderr)
        return proc

    def last_commit(self, branch: str) -> tuple[bytes, dt.datetime]:
        hash, date = (
            self("log", "-1", r"--pretty=format:%H %ci", branch)
            .stdout.strip()
            .split(b" ", maxsplit=1)
        )
        return hash, dt.datetime.strptime(
            date.decode(),
            r"%Y-%m-%d %H:%M:%S %z",
        ).astimezone()

    def branch_contains(self, commit: bytes) -> list[str]:
        return [
            line[2:].decode()
            for line in self("branch", "--contains", commit.decode())
            .stdout.strip()
            .split(b"\n")
        ]

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

    def is_remote(self) -> bool:
        return self.name.startswith("remotes/")

    def remote_name(self) -> str:
        """Name of the remote for this branch"""
        if self.is_remote():
            raise NotInRemoteBranch("Already a remote branch")
        return cast(
            str, self.project.git.config.get(f"branch.{self.name}.remote", "origin")
        )

    def remote_branch(self) -> "Branch | None":
        name = f"remotes/{self.remote_name()}/{self.name}"
        if name in branch_all(merged=True)[1]:
            return Branch(name, self.project)
        return None

    def lag_from_remote_main(self) -> int:
        """This branch junction is n commits behind the remote main branch"""
        remote_head = self.project.git.last_commit(self.remote_name())
        junction = self.project.git("merge-base", self.name, self.remote_name())
        return len(
            self.project.git(
                "log", r"--pretty=format:%H %ci", f"{remote_head}..{junction}"
            )
            .stdout.strip()
            .split(b"\n")
        )

    def pull_request(self) -> "PullRequest | None":
        remote = self.project.remotes[self.remote_name()].pull_request(self.name)
        return remote

    def local_checkout(self) -> str:
        if not self.is_remote():
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
        if fnmatch(self.name, "remotes/*/main"):
            local_name = self.name.split("/", maxsplit=2)[-1]
        else:
            local_name = self.local_checkout()
        self.project.git("merge-tree", "--write-tree", local_name, self.name)

    def last_commit(self) -> tuple[bytes, dt.datetime]:
        return self.project.git.last_commit(self.name)

    def contributors_and_fixers(self) -> tuple[set[str], set[str]]:
        """Contributors and fixers of this branch"""
        main = Branch(self.project.main, self.project)
        remote_main = main.remote_branch()
        if remote_main is not None:
            main_commit = remote_main.last_commit()[0]
        else:
            main_commit = main.last_commit()[0]
        last_commit = self.last_commit()[0]
        logs = [
            "log",
            "--no-merges",
            "--pretty=format:%ae %s",
        ]
        try:
            proc = self.project.git(
                "merge-base", main_commit.decode(), last_commit.decode()
            )
        except GitError as e:
            if not (e.returncode == 1 and e.stdout == b""):
                raise
            # the current branch was never forked from main
        else:
            # this branch was forked from main
            merge_base = proc.stdout.strip().decode()
            if self.name != self.project.main:
                logs.append(f"{merge_base}..{last_commit.decode()}")
        fixers = set()
        contributors = set()
        for line in self.project.git(*logs).stdout.strip().decode().split("\n"):
            author, subject = line.split(" ", maxsplit=1)
            if re.match(r"^(hot|quick|bug)?fix(up!)?[: ]", subject):
                fixers.add(author)
            else:
                contributors.add(author)
        return contributors, fixers


class Forge(ABC):
    """
    Github, Gitlab, Forgejo
    git uses it as remote
    """

    forge_url: str
    remote_name: str
    remote_url: str
    project: "Project"

    def __init__(self, project: "Project", forge_url: str, remote_url: str):
        self.project = project
        self.forge_url = forge_url
        self.remote_url = remote_url
        self.app = "Abstract forge"

    @staticmethod
    @abstractmethod
    def guess_forge(url: str) -> bool:
        pass

    @abstractmethod
    def pull_request(self, branch_name: str) -> "PullRequest | None":
        pass


class PullRequest:
    forge: Forge
    source_branch: Branch
    target_branch: Branch

    def __init__(self, forge: Forge, source_branch: Branch, target_branch: Branch):
        self.forge = forge
        self.source_branch = source_branch
        self.target_branch = target_branch


class Project:
    name: str
    main: str  # main or master ?
    __current_branch: str
    __branches: dict[str, Branch]
    __branches_name: list[str]
    __remotes: dict[str, Forge]
    git: Git

    def __init__(self, git: Git):
        self.git = git
        self.main = "main"
        self.__current_branch, self.__branches_name = branch_all(self.git)
        self.__branches = {}
        self.__remotes = {}

    @property
    def branches(self) -> dict[str, Branch]:
        if not self.__branches:
            for name in self.__branches_name:
                self.__branches[name] = Branch(name, self)
        return self.__branches

    @property
    def current_branch(self) -> Branch:
        return Branch(self.__current_branch, self)

    def _guess_forges(self, lines: list[str]) -> dict[str, Forge]:
        f = {}
        for line in lines:
            name, url, _ = re.split(r"\s+", line, maxsplit=3)
            if name in f:  # assert fetch and pull has same remote
                continue
            forge_url = ""
            if url.startswith("https://"):
                p = urllib.parse.urlparse(url)
                forge_url = f"{p.scheme}://{p.netloc}"
            else:
                domain = url.split(":", maxsplit=2)[0].split("@", maxsplit=2)[-1]
                forge_url = f"https://{domain}"
            f[name] = guess_forge(forge_url)(self, forge_url, url)
        return f

    @property
    def remotes(self) -> dict[str, Forge]:
        if not self.__remotes:
            self.__remotes = self._guess_forges(
                self.git("remote", "--verbose").stdout.strip().decode().split("\n")
            )
        return self.__remotes

    def branches_contains(self, commit: bytes) -> list[Branch]:
        return [Branch(b, self) for b in self.git.branch_contains(commit)]

    def behind(self) -> int:
        self.git("fetch")
        self.git("checkout", self.main)
        status = self.git("status", "-sb")
        self.git("checkout", self.__current_branch)
        m = re.match(rb"\[behind (\d+)\]", status.stdout)
        if m is None:
            raise ParsingException(f"Can't find 'behind' value in '{status.stdout}'")
        return int(m.group(1))

    def fresh_branches(
        self, delta: dt.timedelta = dt.timedelta(days=30), include=None
    ) -> list[Branch]:
        now = dt.datetime.now(dt.timezone.utc)
        fresh: list[Branch] = []
        for name in self.__branches_name:
            if include is not None and not fnmatch(name, include):
                continue
            if fnmatch(name, "remotes/*/HEAD"):
                continue
            branch_date = self.git.last_commit(name)[1]
            if now - branch_date < delta:
                fresh.append(Branch(name, self))
        return fresh

    def test_rebase_with_remote_main(self):
        for cmd in [
            ["checkout", "-b", TEST_BRANCH_NAME],
            ["rebase", "origin/main"],
            ["checkout", self.__current_branch],
            ["branch", "-D", TEST_BRANCH_NAME],
        ]:
            try:
                self.git(*cmd)
            except CalledProcessError as e:
                print(e.args)
                print(e.stderr)

    def remote_main(self, include="remotes/*/*") -> bool:
        """Try to merge every fresh remote branch with main and report conflicts."""
        ok = True
        for branch in self.fresh_branches(include=include):
            print("#", branch.name, end="")
            try:
                branch.try_to_merge_with_main()
            except CalledProcessError as e:
                ok = False
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
        return ok


class GitlabError(CalledProcessError):
    pass


class UnknownForge(Forge):
    def pull_request(self, branch_name: str) -> "PullRequest | None":
        raise NotImplementedError()

    @staticmethod
    def guess_forge(url: str) -> bool:
        return False


class Gitlab(Forge):
    def __init__(self, project: "Project", forge_url: str, remote_url: str):
        super().__init__(project, forge_url, remote_url)
        self.app = "Gitlab"

    def __call__(self, *args):
        try:
            proc = run(
                ["glab"] + list(args) + ["-F", "json"],
                check=True,
                capture_output=True,
                env={**os.environ, "LC_ALL": "C"},
            )
        except CalledProcessError as e:
            raise GitlabError(e.returncode, e.cmd, e.output, e.stderr)
        return proc

    def pull_request(self, branch_name: str) -> PullRequest | None:
        prs = json.loads(self("mr", "list", f"--source-branch={branch_name}").stdout)
        if prs == []:
            return None
        if len(prs) > 1:
            raise TooManyPullRequest(
                "More than one pull request per branch is not Handled"
            )
        pr: dict[str, str] = prs[0]
        return PullRequest(
            self,
            Branch(pr["source_branch"], self.project),
            Branch(
                pr["target_branch"],
                self.project,
            ),
        )

    @staticmethod
    def guess_forge(url: str) -> bool:
        if "gitlab" in url:
            return True
        return "x-gitlab-meta" in yolo_url_open(f"{url}/api/v4/")


def yolo_url_open(url: str) -> Mapping:
    try:
        with urllib.request.urlopen(f"{url}/api/v4/", timeout=3) as f:
            return f.headers
    except HTTPError as e:
        return e.headers


FORGES: list[Forge] = [Gitlab]


def guess_forge(url) -> Forge:
    for forge in FORGES:
        if forge.guess_forge(url):
            return forge
    return UnknownForge


def branch_all(
    git: Git | None = None,
    merged=False,
    all_branches=True,
    include: list[str] | None = None,
    ref="main",
) -> tuple[str, list[str]]:
    if git is None:
        git = Git(os.getcwd())
    proc = git("branch", "--show-current")
    current = proc.stdout.strip().decode()

    command = ["branch"]
    if all_branches:
        command.append("--all")
    if not merged:
        command += ["--no-merged", ref]
    proc = git(*command)
    b = []
    for line in proc.stdout.split(b"\n"):
        line = line.lstrip(b"*").strip()
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
    subparsers.add_parser("remotes")
    subparsers.add_parser("lag", help="Lag from the remote main branch")
    subparsers.add_parser("show")

    args = parser.parse_args(argv)
    git = Git(args.path)
    project = Project(git)

    if args.command == "remote-main":
        if not project.remote_main():
            sys.exit(1)
    elif args.command == "remotes":
        for name, forge in project.remotes.items():
            print(name, forge.app, forge.forge_url, forge.remote_url)
    elif args.command == "lag":
        print(project.current_branch.lag_from_remote_main())
    elif args.command == "show":
        print("Branch:", project.current_branch.name, end="")
        distant = project.current_branch.remote_branch()
        if distant is not None:
            print(" ->", distant.name)
        else:
            print()
        contributors, fixers = project.current_branch.contributors_and_fixers()
        print("Contributors:", ", ".join(contributors), end="")
        if len(fixers):
            print(f" and fixes by {', '.join(fixers)}")
        else:
            print()
    else:  # unreachable: required=True makes argparse exit on missing/unknown command
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
