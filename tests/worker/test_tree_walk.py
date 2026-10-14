"""The descriptor walk under the storage measure and `item_paths.remove_tree`:
going back up past the folders it holds descriptors for, by `..`."""

from __future__ import annotations

import logging
import os
import stat

import pytest
from support.harness import write

from kraft.worker import tree_walk


@pytest.mark.parametrize("moved", [False, True], ids=["in-place", "moved-out"])
def test_a_folder_gone_back_up_to_by_dotdot_is_the_one_come_down_through(tmp_path, caplog, moved):
    """Holding descriptors for only the deepest two folders, the walk goes
    back up to `x/y` by `z/..`. When a worker has moved `z` out of `y` by
    then, `..` is another folder: the walk does not take it for `y`, so
    nothing is done in it, and goes on with what comes after `x` at the top."""
    write(tmp_path, "top/x/y/z/w/leaf", "")
    write(tmp_path, "top/after", "")
    (tmp_path / "elsewhere").mkdir()
    entered, left = [], []

    def enter(_dir_fd, name, st):
        entered.append(name)
        if name == "leaf" and moved:
            (tmp_path / "top/x/y/z").rename(tmp_path / "elsewhere/z")
        return stat.S_ISDIR(st.st_mode)

    top = os.open(tmp_path / "top", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level(logging.WARNING, logger=tree_walk.__name__):
            tree_walk.walk(
                top,
                enter,
                leave=lambda _dir_fd, name: left.append(name),
                names=["x", "after"],
                held=2,
            )
    finally:
        os.close(top)

    assert entered == ["x", "y", "z", "w", "leaf", "after"]
    if moved:
        assert left == ["w"]
        assert "moved while it was walked" in caplog.text
    else:
        assert left == ["w", "z", "y", "x"]
        assert caplog.text == ""
