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


_PAIR = [(10, 20, 300, 5, 0.5), (0, 0, 0, 1000, 0.1)]


@pytest.mark.parametrize(
    ("sessions", "line"),
    [
        (
            _PAIR,
            "usage 1,335 tokens · 10 in · 20 cache write · 300 cache read · 1,005 out · $0.600",
        ),
        (
            [*_PAIR, (2000, None, None, 1, 0.1)],
            "usage 3,336 tokens · 2,010 in (cache not split on older sessions) · 20 cache "
            "write · 300 cache read · 1,006 out · $0.700",
        ),
        (
            # one session told its output, one has not
            [(10, 20, 300, 5, 0.5), (7, 0, 0, None, 0.1)],
            "usage 342 tokens · 17 in · 20 cache write · 300 cache read · at least 5 out · $0.600",
        ),
        (
            [(7, 0, 0, None, 0.1)],
            "usage 7 tokens · 7 in · 0 cache write · 0 cache read · out not known · $0.100",
        ),
    ],
    ids=["split", "older-rows-unsplit", "output-partly-told", "output-not-told"],
)
def test_show_prints_each_kind_of_token(app, capsys, make_item, repo, sessions, line):
    """Dollars as the stop reasons and the web UI's meter print them: a tenth
    of a cent under a dollar, never rounded to cents."""
    wid = make_item(repo)
    for i, s in enumerate(sessions):
        _spent(wid, f"s{i}", *s)

    cli.main(["view", "show", wid])
    out = capsys.readouterr().out
    assert line in " ".join(out.split())
