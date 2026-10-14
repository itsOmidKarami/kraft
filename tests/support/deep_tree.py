"""A chain of folders nested far deeper than a path can name, as a worker can
make one at almost no cost: what a walk of a worker's folder must survive.

    deep_chain(tmp_path / "run" / "worktrees" / "a", 5000, leaf=b"x" * 4096)
    # tmp_path/run/worktrees/a/d/d/.../d/big, 5000 `d`s deep
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def deep_chain(base: Path, depth: int, *, leaf: bytes = b"") -> None:
    """`base` made (with its parents), and `depth` folders named `d` nested
    in it, each made and opened relative to the one above, so no path is
    ever longer than one name; a file `big` holding `leaf` at the bottom."""
    base.mkdir(parents=True, exist_ok=True)
    folder = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(depth):
            os.mkdir("d", dir_fd=folder)
            deeper = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=folder)
            os.close(folder)
            folder = deeper
        big = os.open("big", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=folder)
        try:
            os.write(big, leaf)
        finally:
            os.close(big)
    finally:
        os.close(folder)


# Counts every `open` audit event (`os.open`, `open`), raised from C however
# the caller bound the function, from the moment `setup` has run.
_COUNTING = """
import json, sys
exec(sys.argv[1])
opens = []
sys.addaudithook(lambda event, args: opens.append(1) if event == "open" else None)
result = eval(sys.argv[2])
print(json.dumps({"opens": len(opens), "result": result}))
"""


def opens_during(setup: str, call: str) -> tuple[int, object]:
    """Run `setup`, then evaluate `call`, in a fresh Python; how many files
    and folders `call` opened, and what it returned (as JSON). In a child,
    so neither the count nor what `setup` changes touches the test's own
    process.

        opens, used = opens_during("from kraft import storage", "storage.measure('run').items")
    """
    done = subprocess.run(
        [sys.executable, "-c", _COUNTING, setup, call],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    return seen["opens"], seen["result"]
