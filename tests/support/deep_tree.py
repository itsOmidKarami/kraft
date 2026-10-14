"""A chain of folders nested far deeper than a path can name, as a worker can
make one at almost no cost: what a walk of a worker's folder must survive.

    deep_chain(tmp_path / "run" / "worktrees" / "a", 5000, leaf=b"x" * 4096)
    # tmp_path/run/worktrees/a/d/d/.../d/big, 5000 `d`s deep
"""

from __future__ import annotations

import os
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
