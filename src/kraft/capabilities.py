"""What this version of Kraft can do that an older seeded config cannot.

`$KRAFT_HOME/templates/` is seeded once and never overwritten
(`cli/admin.py`'s `seed_home`), so every capability shipped after an operator's
install is invisible to them. Measured on a real install 2026-09-18: two
capabilities shipped inside a week were not running, and the P1 bug one of them
fixed was closed in the tracker while still live in production.

Hand-maintained on purpose, and deliberately NOT a diff of shipped-vs-live
config. A live registry carries operator intent the shipped defaults do not --
per-hook `model`, `escalate_model` and `effort` choices -- so a diff reports
every deliberate edit as drift, and applying one destroys the edits. This list
answers the useful question instead: what is available, and how do I take it.

**Add an entry in the same change that adds the capability.** One shipped
without an entry is invisible to every existing install, which is the whole
failure this exists to close.
"""

from __future__ import annotations

from dataclasses import dataclass


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
MANIFEST: tuple[Capability, ...] = (
    Capability(
        version="0.67.0",
        name="extends",
        what="compose a chain template instead of copying all of its nodes",
        how="add `extends: default` to your template and delete the nodes you inherit; "
        "`remove`, `insert_before` and `insert_after` adjust what you got",
    ),
    Capability(
        version="0.67.0",
        name="defaults",
        what="settings applied to every `kind: agent` binding in registry.yaml",
        how="add a top-level `defaults:\\n  agent: { steering: [...] }` block and delete "
        "the per-hook copies it replaces",
    ),
    Capability(
        version="0.67.2",
        name="on_failure (binding-level)",
        what="a repair that travels with a task instead of with one node, and "
        "re-dispatches just that task",
        how="add `on_failure: [on.ci.repair]` to the `on.ci.poll` binding in "
        "registry.yaml, and bind `on.ci.repair` beside it",
    ),
)


def added_since(version: str | None) -> list[Capability]:
    """Manifest entries strictly newer than `version`.

    `None` -- a home seeded before stamping existed -- yields everything: it
    knows nothing about itself, and the honest answer is the whole list.
    """
    if version is None:
        return list(MANIFEST)
    floor = _key(version)
    return [c for c in MANIFEST if _key(c.version) > floor]
