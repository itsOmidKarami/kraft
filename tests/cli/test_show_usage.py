"""`kraft view show` prints what an item spent, each kind of input token on its
own (Ruling 211), and says so when older sessions never recorded the split."""

import os
import sqlite3
from pathlib import Path

import pytest

from kraft import cli


def _spent(wid, sid, tokens_in, write, read, out, cost):
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
            "result_path, status, created_at, tokens_in, tokens_cache_write, "
            "tokens_cache_read, tokens_out, cost_usd) VALUES (?, ?, 'verify', 'on.test.run', "
            "'/l', '/r', 'done', 'now', ?, ?, ?, ?, ?)",
            (sid, wid, tokens_in, write, read, out, cost),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("older", "line"),
    [
        (
            False,
            "usage 1,335 tokens · 10 in · 20 cache write · 300 cache read · 1,005 out · $0.60",
        ),
        (
            True,
            "usage 3,336 tokens · 2,010 in (cache not split on older sessions) · 20 cache "
            "write · 300 cache read · 1,006 out · $0.70",
        ),
    ],
    ids=["split", "older-rows-unsplit"],
)
def test_show_prints_each_kind_of_token(app, capsys, make_item, repo, older, line):
    wid = make_item(repo)
    _spent(wid, "s1", 10, 20, 300, 5, 0.5)
    _spent(wid, "s2", 0, 0, 0, 1000, 0.1)
    if older:
        _spent(wid, "s3", 2000, None, None, 1, 0.1)

    cli.main(["view", "show", wid])
    out = capsys.readouterr().out
    assert line in " ".join(out.split())
