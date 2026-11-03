"""What this version of Kraft can do that an older seeded config cannot.

`$KRAFT_HOME/config/` is seeded once and never overwritten
(`cli/admin.py`'s `seed_home`), so every capability shipped after an operator's
install is invisible to them. Measured on a real install 2026-09-18: two
capabilities shipped inside a week were not running, and the P1 bug one of them
fixed was closed in the tracker while still live in production.

Hand-maintained on purpose, and deliberately NOT a diff of shipped-vs-live
config. A live library carries operator intent the shipped defaults do not --
per-task `model` and `effort` choices -- so a diff reports
every deliberate edit as drift, and applying one destroys the edits. This list
answers the useful question instead: what is available, and how do I take it.

**Settings files only.** Chains and the library come from the built-in
`kraft-default` add-on, which every release updates by itself. Add an entry
in the same change that adds a capability to a settings file (`harnesses.yaml`
and the like): one shipped without an entry is invisible to every existing
install, which is the whole failure this exists to close.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Capability:
    #: The release this landed in.
    version: str
    #: The config key or feature name, as an operator would grep for it.
    name: str
    #: One line: what it does.
    what: str
    #: One line: the edit that adopts it.
    how: str
    #: The file under the live templates dir, then the key path in it, that
    #: exists once the edit is made: a hand-merged capability is not listed
    #: again. Empty means adoption cannot be told.
    present: tuple[str, ...] = ()


def _key(version: str) -> tuple[int, ...]:
    """A sortable key for a release string. Anything unparseable sorts oldest,
    so a hand-edited or truncated stamp advertises everything rather than
    raising inside a health check."""
    parts = version.split("+")[0].split("-")[0].split(".")
    out: list[int] = []
    for p in parts:
        if not p.isdigit():
            return (-1,)
        out.append(int(p))
    return tuple(out) or (-1,)


#: Oldest first. `added_since` relies on this order for its output.
#:
#: Settings files only. Entries about `library.yaml` and chains were retired when
#: Kraft's own chains and library became the built-in `kraft-default` add-on,
#: which an upgrade updates and `doctor`'s kraft-default row reports on. The
#: `profiles` entry went the same way: the package supplies the tiers a home
#: lacks, and that row says so.
MANIFEST: tuple[Capability, ...] = ()


def adopted(capability: Capability, live_dir: Path) -> bool:
    """Whether the live templates already hold what `capability` adds: its
    `present` key path exists, a list's step matching one of its items. An
    unreadable file is not adopted, so the row says too much rather than too
    little."""
    if not capability.present:
        return False
    file, *keys = capability.present
    try:
        node = yaml.safe_load((live_dir / file).read_text())
    except (OSError, yaml.YAMLError):
        return False
    for key in keys:
        if isinstance(node, list) and key in node:
            node = key
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return False
    return True


def added_since(version: str | None) -> list[Capability]:
    """Manifest entries strictly newer than `version`.

    `None` -- a home seeded before stamping existed -- yields everything: it
    knows nothing about itself, and the honest answer is the whole list.
    """
    if version is None:
        return list(MANIFEST)
    floor = _key(version)
    return [c for c in MANIFEST if _key(c.version) > floor]
