# FEATURES.md — oups.py feature roadmap

Status legend:

- ✅ **Implemented** — shipped and exercised (tests where applicable)
- 🟡 **Partial** — the data/logic exists, the user-facing part is incomplete
- 🚧 **Planned** — the author's explicit goals, not implemented yet
- 💡 **Proposed** — ideas worth evaluating, not committed to yet

## Positioning

`oups.py` is a **local git safety net**. Primary audience: people who do not
yet master git workflows — it should catch the mistakes beginners make *before*
they hurt. After a few smoke tests on big projects, it is also relevant for
ambitious git workflows.

It reads the local repository (fetch only) and — when a known forge remote is
configured — PR/MR metadata through the `gh` / `glab` CLIs (read-only). It is
**not** a CI, and it does **not** replace a forge (GitHub / GitLab / Forgejo):
no pipelines, no code review, no branch protection, no PR/MR creation or
automation. It never pushes, force-pushes, rebases or merges on its own. The
user keeps the decision; oups provides the information.

## Features

### ✅ Implemented

#### 1. Can my reference branch be rebased without conflict?

`oups.py remote-main` fetches, then tries every fresh remote branch against
`main` using `git merge-tree --write-tree` (a real merge attempt, no checkout
needed). It reports `✅` when the rebase is clean, `🔥` on conflict — with the
conflict details in a `STDOUT:` / `STDERR:` block. Exit code: `0` when
everything merges cleanly, `1` when at least one branch conflicts, `2` on CLI
errors. Covered by the functional test suite.

#### 2. Branch status report — `oups.py show`

A per-branch status report, written as an early-return class (`Show`) with a
small `Output` helper (coloured advice on TTY, plural-aware counts). Sections:

- **Local / Remote**: the branch and its remote-tracking branch, with the
  signed **gap** (ahead / behind / in sync) and a plain-language advice line
  (`Use 'git pull' to integrate changes` / `Use 'git push' to publish`);
- **Contributions**: contributors (`by:`) and fix-only authors (`fix-only:`),
  from `contributors_and_fixers()` (see #3);
- **Main**: gap vs local `main` and vs remote `main`, with the matching advice;
- **Pull request**: for a known forge, the PR/MR title, id, draft/state and
  commenters (see #3).

Known polish items (the report is not frozen yet): no test locks the exact
output text, a few cosmetic glitches remain (e.g. double space after
`branch:`, a non-f-string `'{pr.title}'` placeholder, a self-referential
rebase advice), and the `Main` section needs semantic review.

#### 3. Who contributed to my branch since it forked? (people to inform)

Two complementary sources, both implemented:

- **Git side** — `contributors_and_fixers()`: commits on the branch since the
  merge-base with remote `main`, excluding merge commits (`--no-merges`) and
  **drive-by fixes** (subjects matching `fix(up!)?` / `build(deps)` prefixes,
  plus `[bot]` authors). Returns `(contributors, fixers)` as author emails.
- **Forge side** — PR/MR participants, read-only via `gh` / `glab`:
  author, assignees, reviewers, commenters, merged/closed-by, commit authors,
  head ref OID. Forge detection by remote URL heuristic (GitHub, GitLab), with
  an `UnknownForge` fallback that degrades gracefully. Both CLIs are
  subprocess-isolated with `LC_ALL=C`.

The remaining glue — a dedicated "people to warn before `push -f`" section
aggregating both lists — is tracked in #6.

### 🟡 Partial

#### 4. Did I forget to pull before committing locally?

The **data** is done: signed ahead/behind counts (`gap`, `gap_from_remote`,
`gap_from_remote_main`) surface in `show` with the matching advice. The
**trigger** is missing: there is no `pre-commit` hook yet that runs this check
before every commit (see #7).

### 🚧 Planned (roadmap)

#### 5. Do the other mergeable branches conflict with mine?

Today every branch is checked against `main` only (`remote-main`). This
feature does the opposite direction: pick **my** branch and check it against
the other fresh, mergeable branches (pairwise `git merge-tree --write-tree
<mine> <other>`). Answer: "branch X conflicts with branch Y" — useful before
opening a PR that would collide with a sibling PR.

#### 6. Force-push warning — "people to warn before `push -f`"

All the data is already produced (feature #3 lists; feature #4 gap). Remaining
work: aggregate the git contributors and the PR participants into one
actionable "inform these people" section in `show`, and warn explicitly when
the branch has **diverged** (push would require `--force`).

### 💡 Proposed (ideas)

#### 7. Hook installer — `oups.py install-hooks`

Write `.git/hooks/pre-commit` and `.git/hooks/post-commit` so the checks run
**systematically**, without the user having to remember to call the CLI. Rules:
hooks run the fast checks only (feature 4, conflict markers); the expensive
report (features 1–3) stays a CLI command. Uninstalling with
`oups.py uninstall-hooks` (or `--remove`).

#### 8. Mid-operation detection

Detect an in-progress rebase / merge / cherry-pick (`.git/rebase-merge/`,
`.git/MERGE_HEAD`) and say exactly how to resume or abort. Beginners get stuck
here with no idea what state their repo is in.

#### 9. Conflict-marker scan

Scan the staged files for `<<<<<<<` markers before commit — the classic
"committed the conflict by accident" bug.

#### 10. Staleness / cleanup report

List branches untouched for N days (`fresh_branches` already implements the
delta logic) as **deletion candidates**, so the repo does not rot.

#### 11. Beginner-friendly output polish

Short, jargon-light messages with an explicit "what it means / what to do"
section; no git internals unless `--verbose`. `show` and the `Output` helper
are the vehicle; the CLI stays the power-user interface, the hooks stay silent
unless there is something to say.

## Non-goals

- **Not a CI**: no build/test orchestration, no pipelines, no artifacts.
- **Not a forge client**: no PR/MR creation or automation, no code review, no
  branch protection, no status checks on the forge (that is CI's job). The
  forge layer only *reads* PR/MR metadata for the current repository.
- **No automatic action**: oups reports and advises; it never pushes,
  force-pushes, rebases, merges or deletes by itself.
- **Local-first**: read + fetch only, plus read-only `gh`/`glab` calls. No
  server-side state, no daemon, no configuration to deploy.