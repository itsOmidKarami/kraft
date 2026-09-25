"""Write the release version into every plugin manifest.

Every manifest moves together: one release, one version, in each file.

Usage: python3 dev/stamp_plugin_versions.py <version>   # no leading v
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: Every manifest a release stamps. The caller passes a version and nothing
#: else, so this list is the script's own knowledge -- a plugin added to
#: `plugins/` without a line here keeps whatever version it was committed with.
MANIFESTS = (
    _ROOT / "plugins" / "kraft" / ".claude-plugin" / "plugin.json",
    _ROOT / "plugins" / "kraft-lite" / ".claude-plugin" / "plugin.json",
    # The VS Code extension ships in the same release at the same version.
    _ROOT / "vscode" / "package.json",
)

_VERSION = re.compile(r"\d+\.\d+\.\d+")


def stamp(version: str, manifests=MANIFESTS) -> None:
    """Set `version` in each manifest, leaving every other field alone.

    Validates before writing anything: a half-stamped pair is a marketplace
    where one plugin claims a release the other does not.
    """
    if not _VERSION.fullmatch(version):
        raise ValueError(f"{version!r} is not a bare X.Y.Z version (strip the leading v)")
    for path in manifests:
        blob = json.loads(path.read_text())
        blob["version"] = version
        path.write_text(json.dumps(blob, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    stamp(sys.argv[1])
    for manifest in MANIFESTS:
        print(f"stamped {manifest.relative_to(_ROOT)} at {sys.argv[1]}")
