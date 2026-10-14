"""Run code as a daemon without root's file powers would.

The suite often runs as root (a container), where a folder's mode stops
nothing: root may unlink inside a `chmod 555` folder and list a `chmod 000`
one. A test about what a non-root daemon can do runs its code here instead:
in a fresh Python, under root with `CAP_DAC_OVERRIDE`, `CAP_DAC_READ_SEARCH`
and `CAP_FOWNER` dropped from the bounding set (`setpriv`), and as itself
otherwise, where it has none of them to drop.

    done = run_unprivileged("import sys; print(sys.argv[1])", "hi")
    assert done.stdout == "hi\\n"
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

_DROP = "-dac_override,-dac_read_search,-fowner"


def run_unprivileged(code: str, *args: str, env: dict | None = None):
    """`python -c code *args` without root's file powers; the finished
    process (`check=False`, output captured as text)."""
    prefix: list[str] = []
    if os.geteuid() == 0:
        setpriv = shutil.which("setpriv")
        if setpriv is None:
            pytest.skip("running as root without setpriv: cannot drop root's file powers")
        prefix = [setpriv, "--bounding-set", _DROP, "--"]
    return subprocess.run(
        [*prefix, sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
        timeout=60,
    )
