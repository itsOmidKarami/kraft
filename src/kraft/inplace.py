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
    return folder_for(run_dirs, row, store.materialized_chain_of(row))


def folder_for(run_dirs, row, chain) -> Path:
    """`item_folder` for a caller that already parsed the row's snapshot."""
    in_place = chain is not None and chain.target.in_place
    return Path(row["repo"]) if in_place else run_dirs.worktrees / row["id"]
