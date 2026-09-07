"""Fail when the published surface of plugins/kraft-lite changed without a version bump.

`main` on the public repo is force-pushed on every publish, so its tags are the
only fixed points in that history. A plugin change that ships without a bump has
no tag it can be published under: `just lite-publish` refuses, and until somebody
tries, `main` here holds plugin content that matches no released version.

Usage: python3 dev/check_lite_version.py <base-ref> <head-ref>
"""

from __future__ import annotations

import json
import subprocess
import sys

MANIFEST = "plugins/kraft-lite/.claude-plugin/plugin.json"

#: What a user of the plugin actually receives. Tests, README, LICENSE and the
#: CI workflow are published too, but changing them alters nothing about how the
#: plugin behaves, and demanding a release for a typo fix would train people to
#: bump without meaning it.
SURFACE = (
    "plugins/kraft-lite/kl.py",
    "plugins/kraft-lite/skills",
    "plugins/kraft-lite/chains",
    "plugins/kraft-lite/.claude-plugin",
)


def _run(args: list[str]) -> tuple[int, str]:
    done = subprocess.run(args, capture_output=True, text=True)
    return done.returncode, done.stdout


def changed_surface(base: str, head: str) -> list[str]:
    code, out = _run(["git", "diff", "--name-only", f"{base}...{head}", "--", *SURFACE])
    if code != 0:
        raise SystemExit(f"check_lite_version: git diff {base}...{head} failed")
    return [line for line in out.splitlines() if line.strip()]


def version_at(ref: str) -> str | None:
    """None when the ref predates the manifest — a first release bumps nothing."""
    code, out = _run(["git", "show", f"{ref}:{MANIFEST}"])
    if code != 0:
        return None
    return json.loads(out)["version"]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit(__doc__)
    base, head = argv

    touched = changed_surface(base, head)
    if not touched:
        print("check_lite_version: no change to the plugin's published surface")
        return 0

    before, after = version_at(base), version_at(head)
    if before is None:
        print(f"check_lite_version: {MANIFEST} is new, nothing to bump against")
        return 0
    if before != after:
        print(f"check_lite_version: {before} -> {after}, ok")
        return 0

    listed = "\n  ".join(touched)
    raise SystemExit(
        f"check_lite_version: the plugin's published surface changed but its version "
        f"is still {before}.\n  {listed}\n\n"
        f'Bump "version" in {MANIFEST}, then `just lite-publish` after this merges. '
        "Tags are the only fixed points in the published history, so a change with no "
        "bump has nothing it can be released as."
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
