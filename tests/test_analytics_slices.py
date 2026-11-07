"""GET /analytics for a sliced item: a parent and its slice children are one item
(`kraft.store.slices`), with the children's work rolled into it."""

from __future__ import annotations

import pytest
from test_analytics import NOW, _at, _event, _item, _session

from kraft import analytics, db
from kraft.usage import Usage


@pytest.mark.parametrize(
    ("filters", "n"),
    [
        pytest.param({}, 1, id="unfiltered"),
        pytest.param({"repo": "/a", "template": "default"}, 1, id="parent-repo-and-chain"),
        pytest.param({"repo": "/z"}, 0, id="child-repo-is-not-the-items"),
        pytest.param({"template": "quick-task"}, 0, id="child-chain-is-not-the-items"),
    ],
)
def test_a_sliced_item_counts_once_with_its_childrens_work(tmp_path, filters, n):
    """A parent and its two slices are one item: their spend, repairs and stops add
    to it, and lead time is the parent's own span. The slices are stored in another
    repo and chain, so a filter that matched them instead of the parent would show."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    for parent, days in (("p", 1), ("q", 10)):  # q completed in the previous window
        _item(conn, parent, repo="/a", status="completed", created=_at(days))
        for c in (f"{parent}-c1", f"{parent}-c2"):
            _item(conn, c, repo="/z", template="quick-task", status="completed", created=_at(days))
            conn.execute("UPDATE work_items SET parent_id = ? WHERE id = ?", (parent, c))
    for wid, rounds in (("p", [0]), ("p-c1", [0, 1]), ("p-c2", [0, 1, 2])):  # three repairs
        for rnd in rounds:
            _session(conn, f"{wid}-{rnd}", wid, "verify", round=rnd, u=Usage(1, 1, 0.2))
    _event(conn, "p", "node_started", {"node_id": "verify"}, _at(1))
    _event(conn, "p-c1", "work_item_needs_human", {"reason": "x", "capped": {}}, _at(0.9))
    _event(conn, "p-c1", "work_item_retried", {}, _at(0.8))
    _event(conn, "p", "work_item_completed", {}, _at(0.5))
    a = analytics.compute(conn, range_="7d", now=NOW, **filters)
    conn.close()
    keys = ("work_items", "work_items_run", "completed", "completed_prev", "cost_usd")
    keys += ("fix_cycles", "human_wait_ms", "median_lead_ms", "human_wait_pct")
    assert {k: a["totals"][k] for k in (*keys, "unplanned_touches_per_item")} == {
        **dict.fromkeys(keys[:4], n),
        "cost_usd": pytest.approx(1.2 * n),
        "fix_cycles": 3.0 * n,
        "human_wait_ms": round(0.1 * 86_400_000) * n,  # the slice's stop
        "median_lead_ms": round(0.5 * 86_400_000) * n,  # the parent's own span
        "human_wait_pct": 20 * n,
        "unplanned_touches_per_item": 1.0 * n,
    }
    assert [r["repo"] for r in a["by_repo"]] == ["/a"] * n
    assert a["stop_reasons"] == [{"label": "cap breach", "n": 1}] * n
