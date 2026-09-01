# FEATURES.md — oups.py feature roadmap

Status legend:

- ✅ **Implemented** — shipped, covered by tests where applicable
- 🚧 **Planned** — the author's explicit goals, not implemented yet
- 💡 **Proposed** — ideas worth evaluating, not committed to yet

## Positioning

`oups.py` is a **local git safety net**. Primary audience: people who do not
yet master git workflows — it should catch the mistakes beginners make *before*
they hurt. After a few smoke tests on big projects, it is also relevant for
ambitious git workflows.

It is **not** a CI, and it does **not** replace a forge (GitHub / GitLab /
Forgejo): no pipelines, no code review, no branch protection, no PR/MR
automation. It only reads the local repository, fetches, and reports — it never
pushes, force-pushes, rebases or merges on its own. The user keeps the
decision; oups provides the information.

## Features

### ✅ Implemented

#### 1. Can my reference branch be rebased without conflict?

`oups.py remote-main` fetches, then tries every fresh remote branch against
`main` using `git merge-tree --write-tree` (a real merge attempt, no checkout
needed). It reports `✅` when the rebase is clean, `🔥` on conflict — with the
conflict details in a `STDOUT:` / `STDERR:` block. Exit code: `0` when
everything merges cleanly, `1` when at least one branch conflicts, `2` on CLI
errors.

### 🚧 Planned (roadmap)

#### 2. Who contributed to my branch since it forked?

List the people who have commits on my branch since it diverged from the
reference (`main..branch`), **excluding merge commits and drive-by fixes**, so
that if I need to rebase — and therefore `push -f` — I know who to inform that
their work is about to be rewritten.

> Existing building block: `logs()` / `parse_log()` already parse
> `git log --format=fuller` (commit, author, author date, committer, message)
> per branch. The feature is a new subcommand (e.g. `oups.py contributors
> <branch>`) aggregating authors with their commit counts.

#### 3. Do the other mergeable branches conflict with mine?

Today every branch is checked against `main` only. This feature does the
opposite direction: pick **my** branch and check it against the other fresh,
mergeable branches (pairwise `git merge-tree --write-tree <mine> <other>`).
Answer: "branch X conflicts with branch Y" — useful before opening a PR that
would collide with a sibling PR.

#### 4. Did I forget to pull before committing locally?

Pre-commit check: is the current branch **behind** its remote? Warn "the remote
has commits you don't have yet — pull before you commit, or your local commit
will diverge". This is the beginner's classic and it is exactly what a
`pre-commit` hook is for.

### 💡 Proposed (ideas)

#### 5. Hook installer — `oups.py install-hooks`

Write `.git/hooks/pre-commit` and `.git/hooks/post-commit` so the checks run
**systematically**, without the user having to remember to call the CLI. Rules:
hooks run the fast checks only (feature 4, conflict markers); the expensive
report (features 1–3) stays a CLI command. Uninstalling with
`oups.py uninstall-hooks` (or `--remove`).

#### 6. Force-push awareness

A `pre-push` hook (and/or a `oups.py status` section) that detects when the
local branch has **diverged** from its remote — i.e. a push would need
`--force` — and reminds me to look at the feature-2 contributor list before
doing it.

#### 7. Mid-operation detection

Detect an in-progress rebase / merge / cherry-pick (`.git/rebase-merge/`,
`.git/MERGE_HEAD`) and say exactly how to resume or abort. Beginners get stuck
here with no idea what state their repo is in.

#### 8. Conflict-marker scan

Scan the staged files for `<<<<<<<` markers before commit — the classic
"committed the conflict by accident" bug.

#### 9. Staleness / cleanup report

List branches untouched for N days (`fresh_branches` already implements the
delta logic) as **deletion candidates**, so the repo does not rot.

#### 10. Beginner-friendly output

Short, jargon-light messages with an explicit "what it means / what to do"
section; no git internals unless `--verbose`. The CLI stays the power-user
interface, the hooks stay silent unless there is something to say.

## Non-goals

- **Not a CI**: no build/test orchestration, no pipelines, no artifacts.
- **Not a forge client**: no PR/MR creation or status, no code review, no
  branch protection, no status checks on the forge (that is CI's job).
- **No automatic action**: oups reports and advises; it never pushes,
  force-pushes, rebases, merges or deletes by itself.
- **Local-first**: read + fetch only. No server-side state, no daemon, no
  configuration to deploy.