"""Every npm lockfile pins each package by hash, at its own registry tarball.

`npm ci` checks a download against its `integrity`; an entry without one is
installed by name and version alone. `resolved` must be the package's own
tarball on the public registry: a URL elsewhere, or another package's
tarball and hash, is how a lockfile edit swaps in different code while the
name and version still read right.
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


def _tarball(path: str, entry: dict) -> str:
    name = entry.get("name") or path.rsplit("node_modules/", 1)[1]
    return f"https://registry.npmjs.org/{name}/-/{name.rsplit('/', 1)[-1]}-{entry['version']}.tgz"


@pytest.mark.parametrize("lockfile", _LOCKFILES, ids=lambda p: p.split("/package-lock")[0])
def test_every_package_is_pinned_by_hash_at_its_registry_tarball(lockfile):
    packages = json.loads((_ROOT / lockfile).read_text())["packages"]
    # "" is the project itself; a link or a bundled dependency has no download.
    downloaded = {
        k: v for k, v in packages.items() if k and not v.get("link") and not v.get("inBundle")
    }
    assert downloaded
    unhashed = [k for k, v in downloaded.items() if "integrity" not in v]
    assert not unhashed, f"{lockfile}: no integrity, so npm ci checks nothing: {unhashed}"
    elsewhere = [k for k, v in downloaded.items() if v.get("resolved") != _tarball(k, v)]
    assert not elsewhere, (
        f"{lockfile}: resolved is not the package's own registry tarball: {elsewhere}. "
        "Regenerate the lockfile against registry.npmjs.org, or, for a deliberate "
        "non-registry dependency, add an exception here."
    )
