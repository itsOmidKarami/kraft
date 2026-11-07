"""The documents a slice's child item is filed with.

A child's chain is only the slice body, which produces no `spec` or `plan`, so
an agent finds the child's documents through its attachments. At the fork these
two files are written into the child's `filed/` folder and recorded as its
`spec` and `plan` attachments: the parent's approved spec, and the parent's
approved plan with every task outside the slice tagged `[DONE]` and the
`## Slices` section removed.

Their worktree paths do not exist at the fork commit (`_copy_attachments` skips a
path that exists), so ordinary attachment delivery copies them and commits them
as the child's first commit: its *docs commit*, which the join's replay starts
after, so these files never reach the parent.
"""

from __future__ import annotations

from pathlib import Path

from kraft import item_paths
from kraft.slices import plan_for_slice

__all__ = ["slice_attachments", "worktree_path"]


def worktree_path(child_id: str, kind: str) -> str:
    """Where a child's `kind` document lands in its worktree: a folder named for
    the child, so the path is one no fork commit holds."""
    return f".engineering/slices/{child_id}/{kind}.md"


def slice_attachments(
    run_dirs,
    child_id: str,
    *,
    spec_text: str | None,
    plan_text: str,
    keep: frozenset[int],
    repo: Path | str,
) -> list[dict]:
    """Write the child's spec and slice plan into its `filed/` folder and return
    the attachment records for its row. `keep` is the slice's task numbers; every
    other task is tagged `[DONE]` in the plan the child reads."""
    filed = item_paths.filed_dir(run_dirs, child_id)
    filed.mkdir(parents=True, exist_ok=True)
    documents = {"plan": plan_for_slice(plan_text, keep)}
    if spec_text is not None:
        documents = {"spec": spec_text, **documents}
    records = []
    for kind, text in documents.items():
        source = filed / f"{kind}.md"
        source.write_text(text, encoding="utf-8")
        records.append({"kind": kind, "path": worktree_path(child_id, kind), "source": str(source)})
    return records
