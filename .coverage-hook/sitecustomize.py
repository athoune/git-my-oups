"""Coverage startup hook for the git-oups test subprocesses.

Pointing PYTHONPATH at this directory makes every Python process (pytest and
the git-oups subprocesses it spawns) import this module at startup, which
enables coverage.py subprocess tracing. See `make coverage`.
"""

import coverage

coverage.process_startup()
