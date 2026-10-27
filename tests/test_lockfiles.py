"""Every npm lockfile pins each package by hash, from the public registry.

`npm ci` checks a download against its `integrity`; an entry without one is
installed by name and version alone. A `resolved` URL off the registry (a git
URL, a tarball elsewhere) is how a lockfile edit swaps in another package
while the version still reads right.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LOCKFILES = [
    "frontend/package-lock.json",
    "docsite/package-lock.json",
    "vscode/package-lock.json",
    "vscode/dev/screenshots/package-lock.json",
]


@pytest.mark.parametrize("lockfile", _LOCKFILES, ids=lambda p: p.split("/package-lock")[0])
def test_every_package_is_pinned_by_hash_from_the_registry(lockfile):
    packages = json.loads((_ROOT / lockfile).read_text())["packages"]
    # "" is the project itself; a link or a bundled dependency has no download.
    downloaded = {
        k: v for k, v in packages.items() if k and not v.get("link") and not v.get("inBundle")
    }
    assert downloaded
    assert [k for k, v in downloaded.items() if "integrity" not in v] == []
    off_registry = [
        k
        for k, v in downloaded.items()
        if not v.get("resolved", "").startswith("https://registry.npmjs.org/")
    ]
    assert off_registry == []
