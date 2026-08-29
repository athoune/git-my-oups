from oups import parse_log


def test_parse():
    logs = list(
        parse_log(
            """
commit a0ff60250dc7eb9ddb99d005d6dee933fc91b689 (origin/fix/cors-preflight)
Author: abhigyanpatwari <abhigyan1.patwari@gmail.com>
Date:   Mon Apr 6 11:11:06 2026 +0530

    fix(serve): use localhost as default host instead of ::

    Per reviewer feedback, bind to 'localhost' and let the OS decide
    IPv4 vs IPv6 resolution, rather than hardcoding '::' (dual-stack).

    Also updates the stale 127.0.0.1 comment in api.ts and removes a
    redundant ternary in the CORS origin callback.

    Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

commit 6b86b10a97db41b9e3788b66486abeb085ac929d
Author: Abhigyan Patwari <abhigyan@Abhigyans-MacBook-Air.local>
Date:   Sat Apr 4 08:19:53 2026 +0530

    fix(server): bind to dual-stack '::' so localhost works on IPv6-first systems

    The serve command defaulted to 127.0.0.1 (IPv4 only). On systems where
    'localhost' resolves to ::1 (IPv6 loopback), the server was unreachable
    via localhost — browsers showed CORS errors because the request never
    reached the server (no CORS headers returned = browser blocks it).

    Change default host to '::' (dual-stack), which accepts connections on
    both 127.0.0.1 and ::1. The console now shows 'http://localhost:PORT'
    instead of 'http://:::PORT'. Users can still restrict with --host.

    Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
""".encode()
        )
    )
    assert len(logs) == 2
    print(logs[0].commit)
    assert logs[0].commit.startswith(b"a0ff60250dc7eb9ddb99d005d6dee933fc91b689")
    assert logs[0].author.startswith(b"abhigyanpatwari")
    assert logs[0].date == b"Mon Apr 6 11:11:06 2026 +0530"
    assert logs[1].commit == b"6b86b10a97db41b9e3788b66486abeb085ac929d"
