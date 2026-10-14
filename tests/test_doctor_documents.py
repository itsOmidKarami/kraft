"""What `kraft admin doctor` says about documents still in worktrees'
`.engineering/`, from before they moved to `run/items/<id>/`."""

from __future__ import annotations

import pytest

from kraft import doctor


@pytest.mark.parametrize(
    ("old", "says"),
    [(0, "0 worktrees hold"), (2, "2 worktree(s) still hold")],
    ids=["none", "two"],
)
def test_doctor_counts_worktrees_with_old_documents(tmp_path, monkeypatch, old, says):
    """A count to watch, never a failure: the documents are read where they
    are. A link named `.engineering` is not a folder of documents."""
    run = tmp_path / "run"
    for i in range(4):
        (run / "worktrees" / f"w{i}").mkdir(parents=True)
    for i in range(old):
        (run / "worktrees" / f"w{i}" / ".engineering").mkdir()
    (run / "worktrees" / "w3" / ".engineering").symlink_to(tmp_path)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run))

    line = doctor._old_documents_check()

    assert (line["name"], line["ok"], line["warn"]) == ("old documents", True, bool(old))
    assert says in line["detail"]
