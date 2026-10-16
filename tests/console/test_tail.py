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
        ("old\n", _append("half"), ["old"], []),
        ("old\n", _append("half\nwhole\n"), ["old"], ["half", "whole"]),
    ],
    ids=[
        "backlog-then-new",
        "cut-mid-line",
        "rotated",
        "truncated",
        "missing-at-first",
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
