"""The built-in `kraft-default@kraft` add-on: its identity and where its files
come from. Kraft's own chains and library are read from the installed package,
not from a home."""

from __future__ import annotations

import os
from pathlib import Path

import kraft
from kraft import paths, update
from kraft.addons import fetch, manifest

ID = "kraft-default@kraft"
NAMESPACE = "kraft-default"
COLLECTION = "kraft"


def source_dir() -> Path:
    """Where the built-in's files are: the checkout's `config/` for a source
    build (even beside a stale bundle), else the wheel's bundle, else a
    release-tag checkout's `config/`."""
    checkout = Path(kraft.__file__).resolve().parents[2] / "config"
    if manifest.is_source_build(update.installed()):
        return checkout
    bundled = paths.BUNDLED / "kraft-default"  # at call time: tests move it
    if bundled.is_dir():
        return bundled
    if (checkout / ".kraft" / "addon.json").is_file():
        return checkout
    raise FileNotFoundError("this Kraft was built without its kraft-default; reinstall it")


_memo: tuple[tuple, fetch.Extracted] | None = None


def _stamp(root: Path) -> tuple:
    """(path, mtime_ns, size, ino) of every file `extract_dir` could read."""
    out = [str(root)]
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [d for d in dirnames if d in ("chains", "skills", ".kraft")]
        for name in filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            out.append((str(p), st.st_mtime_ns, st.st_size, st.st_ino))
    return tuple(sorted(out, key=str))


def extracted() -> fetch.Extracted:
    """The built-in as `fetch.extract_dir` reads it, memoized per process until
    a file's stamp changes. Hard links are fine: the package is not an operator
    directory, and uv may link its files from a cache."""
    global _memo
    root = source_dir()
    stamp = _stamp(root)
    if _memo is None or _memo[0] != stamp:
        _memo = (stamp, fetch.extract_dir(root, allow_hardlinks=True))
    return _memo[1]


# --- seed (Task 6) ---
# --- update preview (Tasks 7-8) ---
# --- doctor support (Task 9) ---
