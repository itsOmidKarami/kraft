"""Refuse a release-labelled pull request whose plugin manifests are stale.

`plugin.json` version fields are display-only for a direct install -- nothing
resolves them at fetch time -- but a marketplace showing yesterday's number is
still a lie. This runs in the lint job on every pull request: when a PR is
labelled `release::{major,minor,patch}`, both manifests must already carry the
version that label will tag on merge. Bump them locally first with
`dev/stamp_plugin_versions.py` and commit the result.

Usage: python3 dev/check_plugin_version.py <previous-tag-or-empty> <labels> [manifest...]
Manifests default to stamp_plugin_versions.MANIFESTS when none are given.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import next_tag  # noqa: E402
import stamp_plugin_versions  # noqa: E402


def expected_version(previous: str, labels: str) -> str | None:
    """The bare version this PR's label would tag, or None if nothing to check."""
    impact = next_tag.impact_from_labels(labels)
    if impact is None:
        return None
    tag = next_tag.next_tag(previous or None, impact)
    return tag.lstrip("v") if tag else None


def check(version: str, manifests) -> list[tuple[Path, str]]:
    """Manifests whose committed version does not match, paired with what they have."""
    mismatches = []
    for path in manifests:
        actual = json.loads(path.read_text())["version"]
        if actual != version:
            mismatches.append((path, actual))
    return mismatches


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    previous, labels, *manifest_args = argv[1:]
    manifests = (
        [Path(p) for p in manifest_args] if manifest_args else list(stamp_plugin_versions.MANIFESTS)
    )

    version = expected_version(previous, labels)
    if version is None:
        print("no release label; plugin manifest versions not checked")
        return 0

    mismatches = check(version, manifests)
    if not mismatches:
        print(f"plugin manifests already at {version}")
        return 0

    for path, actual in mismatches:
        print(f"{path}: has {actual}, expected {version}", file=sys.stderr)
    print(f"run: python3 dev/stamp_plugin_versions.py {version}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
