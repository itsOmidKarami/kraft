"""`kraft.worker.worktree_read`: reading a file a worker can write."""

from __future__ import annotations

import pytest

from kraft.worker import worktree_read


def test_an_oversized_worker_file_reads_as_unreadable(tmp_path, monkeypatch):
    """A worker can write a result file of any size, and Kraft reads one
    several times per session: past `READ_MAX_BYTES` it is refused like a
    file that is not regular, after reading one byte past the cap."""
    monkeypatch.setattr(worktree_read, "READ_MAX_BYTES", 8)
    (tmp_path / "small.json").write_bytes(b"12345678")
    (tmp_path / "big.json").write_bytes(b"123456789")

    assert worktree_read.read_no_follow(tmp_path / "small.json") == b"12345678"
    with pytest.raises(OSError, match="larger than 8 bytes"):
        worktree_read.read_no_follow(tmp_path / "big.json")
