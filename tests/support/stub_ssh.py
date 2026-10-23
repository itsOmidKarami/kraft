"""A fake `ssh` for tests: runs the remote command locally."""

from __future__ import annotations

import stat
from pathlib import Path

#: Drops ssh's own options up to `--`, then the host, and runs what is left.
_SCRIPT = """#!/bin/sh
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
shift
exec "$@"
"""


def stub_ssh(directory: Path) -> Path:
    """Write an executable `ssh` into `directory` (made if missing) and return
    it; put it first on `PATH` to have `ssh host cmd...` run `cmd...` here."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "ssh"
    script.write_text(_SCRIPT)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return directory
