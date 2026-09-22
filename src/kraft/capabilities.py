"""What this version of Kraft can do that an older seeded config cannot.

`$KRAFT_HOME/templates/` is seeded once and never overwritten
(`cli/admin.py`'s `seed_home`), so every capability shipped after an operator's
install is invisible to them. Measured on a real install 2026-09-18: two
capabilities shipped inside a week were not running, and the P1 bug one of them
fixed was closed in the tracker while still live in production.

Hand-maintained on purpose, and deliberately NOT a diff of shipped-vs-live
config. A live library carries operator intent the shipped defaults do not --
per-task `model` and `effort` choices -- so a diff reports
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
#:
#: Empty since Template Schema V1: every entry before it told an operator how to
#: adopt a capability in the legacy `registry.yaml` and hook-chain files, which
#: V1 does not read. A V1 home is seeded from the V1 bundle (or replaced by
#: `kraft admin update`), so it already has everything those entries described.
MANIFEST: tuple[Capability, ...] = (
    Capability(
        version="1.0.0",
        name="profiles",
        what="named model tiers (deep, strong, fast) a library agent task selects "
        "instead of spelling out model/effort",
        how="copy the `profiles:` section of the shipped harnesses.yaml into yours, then "
        "set `profile: strong` on a task in place of its `model:`/`effort:`",
    ),
    Capability(
        version="1.0.1",
        name="mr_rebase",
        what="draft_merge_request rebases onto the item's base branch before opening the "
        "draft MR, instead of opening on whatever base the worktree was cut from",
        how=(
            "add to library.yaml's `tasks:`:\n"
            "  mr_rebase:\n"
            "    kind: builtin\n"
            "    ref: kraft.mr_rebase\n"
            "then in chains/default.yaml, replace draft_merge_request's `tasks:` with:\n"
            "    steps:\n"
            "      - id: rebase\n"
            "        tasks:\n"
            "          - id: rebase\n"
            "            extends: mr_rebase\n"
            "      - id: open\n"
            "        tasks:\n"
            "          - id: open\n"
            "            extends: open_draft_mr"
        ),
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
