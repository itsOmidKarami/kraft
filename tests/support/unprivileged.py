"""Run code as a daemon without root's file powers would.

The suite often runs as root (a container), where a folder's mode stops
nothing: root may unlink inside a `chmod 555` folder and list a `chmod 000`
one. A test about what a non-root daemon can do runs its code here instead:
in a fresh Python, under root with `CAP_DAC_OVERRIDE`, `CAP_DAC_READ_SEARCH`
and `CAP_FOWNER` dropped from the bounding and inheritable sets
(`setpriv`), and as itself otherwise, where it has none of them to drop.
Before the first use it checks that this worked, by trying to unlink in a
`chmod 555` folder, and skips the test, saying why, when it did not.

    done = run_unprivileged("import sys; print(sys.argv[1])", "hi")
    assert done.stdout == "hi\\n"
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys

import pytest

_DROP = "-dac_override,-dac_read_search,-fowner"

# Unlinks in a folder its owner cannot write: refused for a daemon without
# root's file powers, allowed with them.
_PROBE = """
import os, shutil, tempfile
folder = tempfile.mkdtemp()
open(os.path.join(folder, "f"), "w").close()
os.chmod(folder, 0o555)
try:
    os.unlink(os.path.join(folder, "f"))
    print("allowed")
except PermissionError:
    print("refused")
finally:
    os.chmod(folder, 0o755)
    shutil.rmtree(folder)
"""


@functools.cache
def _prefix() -> tuple[list[str], str | None]:
    """The command prefix that drops root's file powers, and why the tests
    must skip (None when they need not)."""
    prefix: list[str] = []
    if os.geteuid() == 0:
        setpriv = shutil.which("setpriv")
        if setpriv is None:
            return [], "running as root without setpriv: cannot drop root's file powers"
        prefix = [setpriv, "--bounding-set", _DROP, "--inh-caps", _DROP, "--"]
    probe = subprocess.run(
        [*prefix, sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if probe.stdout.strip() != "refused":
        return prefix, (
            "could not drop root's file powers: unlinking in a mode-555 folder was "
            f"{probe.stdout.strip() or 'not tried'} ({probe.stderr.strip()[-200:]})"
        )
    return prefix, None


def run_unprivileged(code: str, *args: str, env: dict | None = None):
    """`python -c code *args` without root's file powers; the finished
    process (`check=False`, output captured as text)."""
    prefix, skip = _prefix()
    if skip is not None:
        pytest.skip(skip)
    return subprocess.run(
        [*prefix, sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
        timeout=60,
    )
