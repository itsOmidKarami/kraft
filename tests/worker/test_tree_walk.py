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
    nothing is done in it, and goes on with what comes after `x` at the top.
    The log names the folders by `repr`: a worker chooses them, and a
    newline in one must not forge a log line."""
    write(tmp_path, "top/x/y/z\nforged/w/leaf", "")
    write(tmp_path, "top/after", "")
    (tmp_path / "elsewhere").mkdir()
    entered, left = [], []

    def enter(_dir_fd, name, st):
        entered.append(name)
        if name == "leaf" and moved:
            (tmp_path / "top/x/y/z\nforged").rename(tmp_path / "elsewhere/z")
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

    assert entered == ["x", "y", "z\nforged", "w", "leaf", "after"]
    if moved:
        assert left == ["w"]
        [warned] = [r.getMessage() for r in caplog.records]
        assert warned == (
            "'z\\nforged' was moved out of 'x/y' while it was walked; not walking the rest of 'x'"
        )
    else:
        assert left == ["w", "z\nforged", "y", "x"]
        assert caplog.text == ""


@pytest.mark.parametrize("held", [0, -1], ids=["zero", "negative"])
def test_a_walk_holds_at_least_the_folder_it_is_in(tmp_path, held):
    """With no folder held, the walk would let go of the one it just opened
    and resolve the next name against the working directory."""
    write(tmp_path, "top/x/f", "")
    top = os.open(tmp_path / "top", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="held must be at least 1"):
            tree_walk.walk(top, lambda *_: True, held=held)
    finally:
        os.close(top)
