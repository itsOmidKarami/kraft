"""`dispatch._exit_code`: the exit code the board's test rows show, read off
the sidecar a session's launch wrapper wrote. In its own file because
`test_dispatch.py` may not grow (`dev/check_tests.py`)."""

from __future__ import annotations

import os

import pytest

from kraft.executor import dispatch


@pytest.mark.timeout(20)
@pytest.mark.parametrize(
    ("planted", "code"),
    [("file", 3), (None, None), ("symlink", None), ("fifo", None), ("unicode-digit", None)],
    ids=["written", "absent", "symlink-is-unreadable", "fifo-is-unreadable", "unicode-digit"],
)
def test_an_exit_code_is_read_without_following_a_link_or_waiting(tmp_path, planted, code):
    """A session from before the upgrade keeps its exit file beside its
    result in `results/`, which an older escalation thread's codex turns may
    write: a link there is not followed, and a FIFO does not hold the
    server's `GET /work-items/{id}`."""
    result = tmp_path / "results" / "s1.json"
    exit_file = result.with_suffix(".exit")
    exit_file.parent.mkdir()
    if planted == "file":
        exit_file.write_text("3\n")
    elif planted == "symlink":
        (tmp_path / "elsewhere").write_text("3\n")
        exit_file.symlink_to(tmp_path / "elsewhere")
    elif planted == "fifo":
        os.mkfifo(exit_file)
    elif planted == "unicode-digit":
        exit_file.write_text("²")
    row = {"log_path": str(tmp_path / "logs" / "s1.log"), "result_path": str(result)}

    assert dispatch._exit_code(row) == code
