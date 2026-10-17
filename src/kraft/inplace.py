"""Items that run in their repository's own checkout instead of a worktree.

Whether an item runs in place is frozen on its chain snapshot at intake
(`WorkItemTarget.in_place`); everything here reads it from there.
"""

from __future__ import annotations

from pathlib import Path

from kraft import store


def is_in_place(row) -> bool:
    """Whether the item runs in its repository's checkout. False for a row
    with no snapshot, or one that did not select it."""
    chain = store.materialized_chain_of(row)
    return chain is not None and chain.target.in_place


def item_folder(run_dirs, row) -> Path:
    """The folder the item's work happens in: its repository's checkout when
    it runs in place, else its worktree."""
    return Path(row["repo"]) if is_in_place(row) else run_dirs.worktrees / row["id"]
