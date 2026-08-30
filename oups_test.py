from oups import parse_log


def test_parse():
    logs = list(
        parse_log(
            b"""
commit 8291660ced043512988c507c4f342eeffd9cb9f7 (origin/Cenrax-patch-1)
Author:     Subham Kundu <43017632+Cenrax@users.noreply.github.com>
AuthorDate: Tue Aug 18 01:52:48 2026 -0700
Commit:     GitHub <noreply@github.com>
CommitDate: Tue Aug 18 01:52:48 2026 -0700

    Change project title in README

    Updated project title to include 'Akon Labs'.

commit 7f0ab16ffe45846b4bfc7384a362365d94b7bcdb (tag: rc/7f0ab16ffe45846b4bfc7384a362365d94b7bcdb)
Author:     azizur100389 <azizur100389@gmail.com>
AuthorDate: Tue Aug 18 04:39:45 2026 +0100
Commit:     GitHub <noreply@github.com>
CommitDate: Tue Aug 18 04:39:45 2026 +0100

    feat(routes): support JS data route tables (#2972)
"""
        )
    )
    assert len(logs) == 2
    print(logs[0].commit)
    assert logs[0].commit.startswith(b"8291660ced043512988c507c4f342eeffd9cb9f7")
    assert logs[0].author.startswith("Subham Kundu")
    assert logs[0].author_date == b"Tue Aug 18 01:52:48 2026 -0700"
    assert logs[0].commiter.startswith("GitHub")
    assert logs[0].commit_date == b"Tue Aug 18 01:52:48 2026 -0700"
    assert logs[1].commit == b"7f0ab16ffe45846b4bfc7384a362365d94b7bcdb"
