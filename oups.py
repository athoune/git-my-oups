#! /usr/bin/env python3
import argparse
import datetime as dt
import io
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
from typing import Any, cast
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


class RemoteBranchException(Exception):
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
            if error or os.getenv("VERBOSE"):
                sys.stderr.write(
                    f'Error running "git {" ".join(args)}"\n\n{e.stderr.decode()}\n'
                )
            raise GitError(e.returncode, e.cmd, e.output, e.stderr)
        return proc

    def last_commit(self, branch: str) -> tuple[bytes, dt.datetime]:
        hash, date = (
            self("log", "-1", r"--pretty=format:%H %ci", branch)
            .stdout.strip()
            .split(b" ", maxsplit=1)
        )
        return (
            hash,
            dt.datetime.strptime(
                date.decode(),
                r"%Y-%m-%d %H:%M:%S %z",
            ).astimezone(),
        )

    def branch_contains(self, commit: bytes) -> list[str]:
        return [
            line[2:].decode()
            for line in self("branch", "--contains", commit.decode())
            .stdout.strip()
            .split(b"\n")
        ]

    def merge_base(self, commit_a: bytes, commit_b: bytes) -> bytes | None:
        if commit_a == commit_b:
            return None
        try:
            proc = self("merge-base", commit_a.decode(), commit_b.decode())
        except GitError as e:
            if not (e.returncode == 1 and e.stdout == b""):
                raise
            # the current branch was never forked from main
            return None
        # this branch was forked from main
        return proc.stdout.strip()

    def commits_length_from_to(self, commit_from: bytes, commit_to: bytes) -> int:
        """Number of commits from twos hashes."""
        if commit_from == commit_to:
            return 0
        logs = (
            self(
                "log",
                "--pretty=format:%H",
                f"{commit_from.decode()}..{commit_to.decode()}",
            )
            .stdout.strip()
            .split(b"\n")
        )
        if logs == [b""]:
            return 0
        return len(logs)

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

    @property
    def remote(self) -> "Forge":
        return self.project.remotes[self.remote_name()]

    def logs(self) -> list[Log]:
        if not self._logs:
            self._logs = list(logs(self.project.git, self.name))
        return self._logs

    def is_remote(self) -> bool:
        return self.name.startswith("remotes/")

    def remote_name(self) -> str:
        """Name of the remote for this branch"""
        if self.is_remote():
            raise RemoteBranchException("Already a remote branch")
        return cast(
            str, self.project.git.config.get(f"branch.{self.name}.remote", "origin")
        )

    def remote_branch(self) -> "Branch | None":
        name = f"remotes/{self.remote_name()}/{self.name}"
        if name in branch_all(self.project.git, merged=True)[1]:
            return Branch(name, self.project)
        return None

    def lag_from_remote_main(self) -> int:  # [FIXME] use self.lag
        """This branch junction is n commits behind the remote main branch"""
        remote_branch = self.remote_branch()
        if remote_branch is None:
            raise RemoteBranchException(f"branch {self.name} has no remote branch")
        remote_head = remote_branch.last_commit()[0]
        local_head = self.last_commit()[0]
        lag = self.project.git.commits_length_from_to(local_head, remote_head)
        if lag > 0:
            return lag
        return -self.project.git.commits_length_from_to(remote_head, local_head)

    def lag_from_remote(self) -> int:
        remote = self.remote_branch()
        if remote is None:
            return 0
        return self.lag(remote)

    def lag(self, branch: "Branch") -> int:
        last_local, _ = self.last_commit()
        last_remote, _ = branch.last_commit()
        lag = self.project.git.commits_length_from_to(last_remote, last_local)
        if lag > 0:
            return lag
        return -self.project.git.commits_length_from_to(last_local, last_remote)

    def pull_request(self) -> "PullRequest | None":
        return self.remote.pull_request(self.name)

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
            remote_main_last_commit = remote_main.last_commit()[0]
        else:
            remote_main_last_commit = main.last_commit()[0]
        local_last_commit = self.last_commit()[0]
        fixers = set()
        contributors = set()
        logs = [
            "log",
            "--no-merges",
            "--pretty=format:%ae %s",
        ]
        merge_base = self.project.git.merge_base(
            local_last_commit,
            remote_main_last_commit,
        )
        if merge_base in (local_last_commit, remote_main_last_commit):
            # current branch is empty
            return set(), set()
        if merge_base is not None and merge_base != local_last_commit:
            logs.append(f"{merge_base.decode()}..{local_last_commit.decode()}")
        for line in self.project.git(*logs).stdout.strip().decode().split("\n"):
            if line.strip() == "":
                continue
            author, subject = line.split(" ", maxsplit=1)
            if (
                re.match(r"^((hot|quick|bug)?fix(up!)?|build\(deps\))[: ]", subject)
                or "[bot]" in author
            ):
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


class Comment(ABC):
    author: str
    body: str
    createdAt: dt.datetime

    @abstractmethod
    def __init__(self, cm: dict[str, Any]):
        pass


class PullRequest(ABC):
    forge: Forge
    id: str
    source_branch: Branch
    target_branch: Branch
    title: str
    author: str
    assignees: list[str]
    reviewers: list[str]
    draft: bool
    state: str
    merged_by: str | None
    closed_by: str | None
    comments: list[Comment]

    def __init__(self, forge: Forge, pr: dict[str, Any]):
        self.forge = forge

    def commenters(self) -> list[str]:
        return [comment.author for comment in self.comments]


class Project:
    name: str
    main: str  # main or master ?
    __current_branch: str
    __branches: dict[str, Branch]
    __branches_name: list[str]
    __remotes: dict[str, Forge]
    __main_branch: Branch | None
    git: Git

    def __init__(self, git: Git):
        self.git = git
        self.main = "main"
        self.__current_branch, self.__branches_name = branch_all(self.git)
        self.__branches = {}
        self.__remotes = {}
        self.__main_branch = None

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

    def main_branch(self) -> Branch:
        if self.__main_branch is None:
            self.__main_branch = Branch(self.main, self)
        return self.__main_branch

    def remote_main(self, include="remotes/*/*") -> bool:
        """Try to merge every fresh remote branch with main and report conflicts."""
        ok = True
        for branch in self.fresh_branches(include=include):
            print("#", branch.name, end="")
            try:
                branch.try_to_merge_with_main()
            except CalledProcessError as e:
                ok = False
                print(f""" 🔥

Error occurred while merging branch {branch.name}:""")
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


class GithubError(CalledProcessError):
    pass


class UnknownForge(Forge):
    def pull_request(self, branch_name: str) -> "PullRequest | None":
        raise NotImplementedError()

    @staticmethod
    def guess_forge(url: str) -> bool:
        return False


class GitlabPullRequest(PullRequest):
    def __init__(self, forge: Forge, pr: dict[str, Any]):
        super().__init__(forge, pr)
        self.source_branch = Branch(
            pr["source_branch"],
            self.forge.project,
        )
        self.target_branch = Branch(
            pr["target_branch"],
            self.forge.project,
        )
        self.title = pr["title"]
        self.author = pr["author"][
            "username"
        ]  # [FIXME] fetch the mail with another API call
        self.assignees = [a["username"] for a in pr["assignees"]]
        self.reviewers = [a["username"] for a in pr["reviewers"]]
        self.draft = pr["draft"]
        self.state = pr["state"]  # merged …
        self.merged_by = (
            pr["merged_by"]["username"] if pr["merged_by"] is not None else None
        )
        self.closed_by = (
            pr["closed_by"]["username"] if pr["closed_by"] is not None else None
        )


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
        prs: list[dict[str, Any]] = json.loads(
            self("mr", "list", f"--source-branch={branch_name}").stdout
        )
        if prs == []:
            return None
        if len(prs) > 1:
            raise TooManyPullRequest(
                "More than one pull request per branch is not Handled"
            )
        return GitlabPullRequest(self, prs[0])

    @staticmethod
    def guess_forge(url: str) -> bool:
        if "gitlab" in url:
            return True
        return "x-gitlab-meta" in yolo_url_open(f"{url}/api/v4/")


class GithubComment(Comment):
    def __init__(self, cm: dict[str, Any]):
        super().__init__(cm)
        self.__raw = cm
        self.author = cm["author"]["login"]
        self.body = cm["body"]


class GithubPullRequest(PullRequest):
    def __init__(self, forge: Forge, pr: dict[str, Any]):
        super().__init__(forge, pr)
        self.id = pr["number"]
        self.source_branch = Branch(pr["baseRefName"], self.forge.project)
        self.target_branch = Branch(
            pr["headRefName"],
            self.forge.project,
        )
        self.title = pr["title"]
        self.author = pr["author"]["login"]
        self.draft = pr["isDraft"]
        self.state = pr["state"]
        self.merged_by = pr["mergedBy"]["login"] if pr["mergedBy"] is not None else None
        self.comments = [GithubComment(c) for c in pr["comments"]]


class Github(Forge):
    def __init__(self, project: "Project", forge_url: str, remote_url: str):
        super().__init__(project, forge_url, remote_url)
        self.app = "Github"

    def __call__(self, *args):
        try:
            proc = run(
                ["gh"] + list(args),
                check=True,
                capture_output=True,
                env={**os.environ, "LC_ALL": "C"},
            )
        except CalledProcessError as e:
            raise GithubError(e.returncode, e.cmd, e.output, e.stderr)
        return proc

    def pull_request(self, branch_name: str) -> PullRequest | None:
        try:
            proc = self(
                "pr",
                "view",
                branch_name,
                "--json",
                "title,baseRefName,closed,headRefName,title,createdAt,state,updatedAt,isDraft,assignees,author,closed,mergedBy,reviews,id,number,comments",
            )
        except GithubError as e:
            if e.stderr.startswith(b"no pull requests found for branch"):
                return None
            raise
        return GithubPullRequest(self, json.loads(proc.stdout))

    @staticmethod
    def guess_forge(url: str) -> bool:
        return "github" in url


def yolo_url_open(url: str) -> Mapping:
    try:
        with urllib.request.urlopen(f"{url}/api/v4/", timeout=3) as f:
            return f.headers
    except HTTPError as e:
        return e.headers


FORGES: list[Forge] = [Gitlab, Github]


def guess_forge(url) -> Forge:
    for forge in FORGES:
        if forge.guess_forge(url):
            return forge
    return UnknownForge


def branch_all(
    git: Git,
    merged=False,
    all_branches=True,
    include: list[str] | None = None,
    ref="main",
) -> tuple[str, list[str]]:
    current = git("branch", "--show-current").stdout.strip().decode()

    if git("branch").stdout.strip() == b"":  # empty git
        b = []
    else:
        command = ["branch"]
        if all_branches:
            command.append("--all")
        if not merged:
            command.append("--no-merged")
            if not all_branches:
                command.append(ref)
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


def no_remotes_prefix(txt: str) -> str:
    if txt.startswith("remotes/"):
        return txt[len("remotes/") :]
    return txt


class Output:
    def __init__(self):
        self._buff = io.StringIO()

    def write(self, txt: str):
        self._buff.write(txt)

    def getvalue(self) -> str:
        return self._buff.getvalue()

    def advice_style(self):
        if os.isatty(sys.stdout.fileno()):
            self._buff.write("\x1b[33m")

    def reset_style(self):
        if os.isatty(sys.stdout.fileno()):
            self._buff.write("\x1b[39m\x1b[49m")


def show(project: Project) -> str:
    buff = Output()
    distant_branch = project.current_branch.remote_branch()
    buff.write(f"""⎮ Local
⎮   branch: '{project.current_branch.name}'\n""")
    if project.git("branch").stdout.strip() == b"":
        buff.write("⎮   empty\n")
        buff.advice_style()
        buff.write("No commit yet, add files and 'git add' them")
        buff.reset_style()
        return buff.getvalue()

    buff.write("⎮ Remote\n")
    buff.write(
        f"⎮   branch: {"'" + distant_branch.name + "'" if distant_branch is not None else 'none'}\n"
    )
    if distant_branch is not None:
        lag = project.current_branch.lag_from_remote()
        buff.write("⎮   status: ")
        if lag == 0:
            buff.write("in sync")
        elif lag < 0:
            buff.write(f" {-lag} commit")
            if lag < -1:
                buff.write("s")
            buff.write(" behind\n")
            buff.advice_style()
            buff.write("Use 'git pull' to integrate changes")
            buff.reset_style()
        else:
            buff.write(f" {lag} commit")
            if lag > 1:
                buff.write("s")
            buff.write(" ahead\n")
            buff.advice_style()
            buff.write("Use 'git push' to publish")
            buff.reset_style()
        buff.write("\n")

        if (
            project.current_branch.name != project.main
            and project.current_branch.remote_branch() is not None
        ):
            lag_remote_main = project.current_branch.lag_from_remote_main()
            if lag_remote_main > 0:
                buff.write(
                    f"{project.main} is the reference "
                    f"of the fork {project.current_branch.name} "
                    f"but {project.main} is below "
                    f"{no_remotes_prefix(project.current_branch.remote_branch())} "
                    f"by {lag_remote_main} commit",
                )
                if lag_remote_main > 1:
                    buff.write("s")
                buff.write(".\n")

    if project.current_branch.name != project.main:
        contributors, fixers = project.current_branch.contributors_and_fixers()
        buff.write("""⎮ Contributions
""")
        if len(contributors) == 0:
            buff.write("⎮   none\n")
        else:
            buff.write(f"⎮   by: {', '.join(contributors)}\n")
        if len(fixers):
            buff.write(f"⎮   fix-only: {', '.join(fixers)}\n")

    buff.write("⎮ Main\n")
    lag_from_local_main = project.current_branch.lag(project.main_branch())
    buff.write(
        f"⎮   lag from local main: {lag_from_local_main if lag_from_local_main >= 0 else '0 (synced)'}\n"
    )
    lag_from_remote_main = 0
    if project.current_branch.name != project.main:
        lag_from_remote_main = project.current_branch.lag_from_remote_main()
        buff.write(f"⎮   lag from remote main: {lag_from_remote_main}\n")
    if lag_from_local_main < 0:
        buff.advice_style()
        buff.write(
            f"Use 'git rebase {project.main}' to rebase the current branch onto '{project.current_branch.name}'\n"
        )
        buff.reset_style()
    if lag_from_remote_main != 0:
        buff.advice_style()
        buff.write(
            f"Use 'git pull {project.main_branch().remote_name()} {project.main}' to sync remote and local '{project.main}'"
        )
        buff.reset_style()

    if not isinstance(project.current_branch.remote, UnknownForge):
        pr = project.current_branch.pull_request()
        buff.write(f"""⎮ {project.current_branch.remote.app}
⎮   pull request:
⎮     title: '{pr.title if pr is not None else "none"}'
""")
        if pr is not None:
            buff.write(f"""|     id: {pr.id}
⎮     draft: {"true" if pr.draft else "false"}
⎮     state: {pr.state}
""")
            commenters = set(pr.commenters())
            if len(commenters):
                buff.write(f"|     commenters: {', '.join(commenters)}\n")

    return buff.getvalue()


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
        print(show(project))
    else:  # unreachable: required=True makes argparse exit on missing/unknown command
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
