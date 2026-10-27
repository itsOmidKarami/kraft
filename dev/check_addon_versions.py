"""Fail if an add-on of Kraft's own collection changed and kept its `version`.

An instance takes an add-on's update only when its `version` changes, and the
collection is installed from the default branch, so every change that reaches
`main` must carry its own raise: one to `config/library.yaml`, `config/chains/`
or an add-on under `addons/` that keeps the version never reaches an instance
that already took that version. This compares each add-on
`.kraft/collection.json` lists, as the working tree has it, with the base: the
merge base with `origin/main`, or the commit before when that is this one (a
push to `main`), or `--base`. When the files Kraft reads from an add-on differ,
its `version` must be higher, since an update refuses a downgrade. An add-on
the base lacks is new and passes.

    uv run python dev/check_addon_versions.py [--base REF]

Needs `origin/main` (CI's lint job checks out with fetch-depth 0); without it,
it only warns outside CI. Exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from kraft.addons import fetch, manifest
from kraft.addons.review import precedence

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str | None:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def base_commit(root: Path, upstream: str = "origin/main") -> str | None:
    """What this change is measured against; None when there is nothing before it."""
    base = _git(root, "merge-base", "HEAD", upstream)
    if base is not None and base == _git(root, "rev-parse", "HEAD"):
        return _git(root, "rev-parse", "--verify", "--quiet", "HEAD^1")
    return base


def resolve_base(root: Path, given: str | None) -> str | None:
    """`--base` as a commit, refusing one this clone lacks; none given, or a
    push's all-zero `before` (nothing came before it), is `base_commit`'s."""
    if given and given.strip("0"):
        found = _git(root, "rev-parse", "--verify", "--quiet", f"{given}^{{commit}}")
        if found is None:
            raise SystemExit(f"--base {given} is not a commit in this clone")
        return found
    return base_commit(root)


def _version(files) -> str:
    return json.loads(files[manifest.ADDON_JSON][1])["version"]


def problems(root: Path, base: str) -> list[str]:
    git_dir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    listed = json.loads((root / manifest.COLLECTION_JSON).read_text())["addons"]
    found = []
    for entry in listed:
        source = entry["source"]
        try:
            now = fetch.extract_dir(root / source.removeprefix("./")).files
        except fetch.AddonRefused as exc:
            found.append(f"{entry['name']}: Kraft refuses it as it is now: {exc}")
            continue
        try:
            then = fetch.extract_git(git_dir, base, source).files
        except fetch.FetchError:
            continue  # no such directory at the base: new
        except fetch.AddonRefused as exc:
            found.append(f"{entry['name']}: Kraft refuses it at {base[:12]}: {exc}")
            continue
        if manifest.ADDON_JSON not in then or fetch.digest(now) == fetch.digest(then):
            continue  # not yet an add-on at the base, or unchanged
        if precedence(_version(now)) <= precedence(_version(then)):
            found.append(
                f"{entry['name']}: changed since {base[:12]} but version {_version(now)} is "
                f"not above {_version(then)}; raise `version` in "
                f"{source.removeprefix('./')}/{manifest.ADDON_JSON}"
            )
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="the commit to compare with (CI: the commit before a push)")
    args = ap.parse_args()
    base = resolve_base(ROOT, args.base)
    if base is None:
        print("no base to compare with: git fetch origin main", file=sys.stderr)
        # A clone with no origin/main cannot tell; CI always can.
        return 1 if os.environ.get("CI") else 0
    found = problems(ROOT, base)
    for line in found:
        print(line)
    if not found:
        print(f"ok: every add-on that changed since {base[:12]} has a new version")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
