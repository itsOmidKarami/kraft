"""Following `server.log`: the last lines, then what the server writes, across
the rotation at start and a truncation."""

from __future__ import annotations

import asyncio
import os

import pytest

from kraft.console import tail

#: Nine short lines: fewer bytes than the first read takes, more lines than the backlog.
MANY = "".join(f"{n}\n" for n in range(9))


def _append(text: str):
    def change(path):
        with path.open("a") as log:
            log.write(text)

    return change


def _rotate(path):
    # As `kraft admin start` does: the old file moves aside, a new one starts.
    os.replace(path, path.with_name("server.log.1"))
    path.write_text("fresh start\n")


def _truncate(path):
    path.write_text("cut\n")


@pytest.mark.parametrize(
    ("before", "change", "first", "after"),
    [
        (MANY, _append("new\n"), ["6", "7", "8"], ["new"]),
        ("x" * 40 + "\nkept\n", _append("new\n"), ["kept"], ["new"]),
        ("old\n", _rotate, ["old"], ["fresh start"]),
        ("a much longer first line\n", _truncate, ["a much longer first line"], ["cut"]),
        (None, _append("born\n"), [], ["born"]),
        # 50 bytes in 10-byte lines, a 30-byte backlog: the seek lands on a line start.
        (
            "".join(f"line{n:05d}\n" for n in range(5)),
            _append("new\n"),
            ["line00002", "line00003", "line00004"],
            ["new"],
        ),
        ("old\n", _append("half"), ["old"], []),
        ("old\n", _append("half\nwhole\n"), ["old"], ["half", "whole"]),
    ],
    ids=[
        "backlog-then-new",
        "cut-mid-line",
        "rotated",
        "truncated",
        "missing-at-first",
        "seek-on-a-boundary",
        "partial",
        "partial-done",
    ],
)
async def test_the_tail_follows_the_log_through_a_rotation_and_a_truncation(
    tmp_path, monkeypatch, before, change, first, after
):
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    monkeypatch.setattr(tail, "BACKLOG_BYTES", 30)
    path = tmp_path / "server.log"
    if before is not None:
        path.write_text(before)
    got: list[str] = []

    async def read() -> None:
        async for line in tail.follow(path, backlog=3):
            got.append(line)

    reader = asyncio.create_task(read())
    try:
        async with asyncio.timeout(5):
            while len(got) < len(first):
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)  # a poll or more with nothing new
            change(path)
            while len(got) < len(first) + len(after):
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # nothing more arrives: a partial line stays unsaid
    finally:
        reader.cancel()
    assert got == first + after


def _make_a_file(path):
    path.rmdir()
    path.write_text("ok\n")


def _make_it_readable(path):
    path.chmod(0o644)
    _append("ok\n")(path)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs permissions to deny")
@pytest.mark.parametrize("unreadable", ["a-directory", "no-permission"])
async def test_an_unreadable_log_is_waited_for_not_raised(tmp_path, monkeypatch, unreadable):
    """The follower is the console's, not a crash: whatever stops it opening
    the log is read as the log not being there yet."""
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    path = tmp_path / "server.log"
    if unreadable == "a-directory":
        path.mkdir()
        fix = _make_a_file
    else:
        path.write_text("secret\n")
        path.chmod(0)
        fix = _make_it_readable
    got: list[str] = []

    async def read() -> None:
        async for line in tail.follow(path):
            got.append(line)

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.1)  # several polls, refused each time
        assert not reader.done()
        fix(path)
        async with asyncio.timeout(5):
            while "ok" not in got:
                await asyncio.sleep(0.01)
    finally:
        reader.cancel()


async def test_a_backlog_of_none_says_no_old_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    path = tmp_path / "server.log"
    path.write_text("old\n")
    got: list[str] = []

    async def read() -> None:
        async for line in tail.follow(path, backlog=0):
            got.append(line)

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.1)
        _append("new\n")(path)
        async with asyncio.timeout(5):
            while not got:
                await asyncio.sleep(0.01)
    finally:
        reader.cancel()
    assert got == ["new"]
