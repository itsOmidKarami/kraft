from pathlib import Path

import pytest

from kraft.worker import mounts
from kraft.worker.refstore import RefStore


@pytest.mark.parametrize(
    ("rw_dirs", "want"),
    [(["/d/", "/d"], 1), (["/d", "/e"], 2)],
    ids=["trailing-slash-dedupes", "distinct-kept"],
)
def test_rw_dirs_are_keyed_on_path(tmp_path, rw_dirs, want):
    ms = mounts.session_mounts(tmp_path, None, rw_dirs=rw_dirs)
    assert [m for m in ms[1:] if m.src in (Path("/d"), Path("/e"))].__len__() == want


def test_a_direct_store_mounts_the_real_gitdir_with_only_its_branchs_files_writable(tmp_path):
    common = tmp_path / "c"
    admin, git_file = common / "worktrees" / "w", tmp_path / "wt" / ".git"
    for d in (common / "objects", admin):
        d.mkdir(parents=True)
    (admin / "gitdir").write_text("x")
    store = RefStore(common, common, admin, "kraft/fix-x")

    ms = mounts._linked_mounts(git_file, common, admin, store)

    heads = common / "refs" / "heads" / "kraft" / "fix-x"
    assert {(m.dst, m.ro) for m in ms} == {
        (git_file, True),
        (common, True),
        (common / "objects", False),
        (common / "lfs", False),
        (common / "objects" / "info", True),
        (admin, False),
        (admin / "commondir", True),
        (admin / "config.worktree", True),
        (admin / "gitdir", True),
        (heads, False),
        (heads.with_name("fix-x.lock"), False),
        (common / "logs" / "refs" / "heads" / "kraft" / "fix-x", False),
        (common / "packed-refs.lock", False),  # a commit takes it to delete a pseudo-ref
    }
    assert all(m.src == m.dst for m in ms)
