"""A command prefix that proves its caller started it off the terminal."""

from __future__ import annotations

#: `python -c` this, then a command: it runs the command only when it leads a
#: session of its own (`start_new_session`), off any terminal, else exits 1.
SESSION_LEADER_ONLY = (
    "import os, sys\n"
    "if os.getsid(0) != os.getpid():\n"
    "    sys.exit('not in a session of its own')\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)
