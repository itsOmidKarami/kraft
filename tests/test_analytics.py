"""GET /analytics: the aggregates behind design 6b."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from kraft import analytics, db
from kraft.usage import Usage

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _at(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


CHAIN = {
    "template_id": "default",
    "nodes": [
        {
            "id": "verify",
            "tasks": ["on.test.run"],
            "gate_after": None,
            "fix_loop": "verify_fix_loop",
        },
        {
            "id": "human_review",
            "tasks": ["on.human_review.requested"],
            "gate_after": "human_review_approval",
        },
        {"id": "merge", "tasks": ["on.merge"], "gate_after": None},
    ],
}


def _item(conn, wid, *, repo="/a", template="default", status="active", created):
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, wid, repo, template, json.dumps(CHAIN), status, created, created),
    )


def _session(conn, sid, wid, node, *, round=0, status="done", u: Usage | None = None, wall_ms=1000):
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, attempt, created_at, round, tokens_in, tokens_out, cost_usd, "
        "wall_ms) VALUES (?, ?, ?, ?, 'l', 'r', ?, 1, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            wid,
            node,
            f"on.{node}",
            status,
            _at(1),
            round,
            u.tokens_in if u else None,
            u.tokens_out if u else None,
            u.cost_usd if u else None,
            wall_ms,
        ),
    )


def _event(conn, wid, type_, payload, at):
    conn.execute(
        "INSERT INTO events (work_item_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
        (wid, type_, json.dumps(payload), at),
    )


def _fixture(path):
    conn = db._connect(path)
    db.migrate(conn)
    # in range
    _item(conn, "w1", repo="/a", status="completed", created=_at(2))
    conn.execute("UPDATE work_items SET updated_at = ? WHERE id = 'w1'", (_at(1),))
    _session(conn, "s1", "w1", "verify", u=Usage(1000, 100, 0.5), wall_ms=60_000)
    _session(
        conn,
        "s2",
        "w1",
        "verify",
        round=1,
        status="capped_out",
        u=Usage(500, 50, 0.25),
        wall_ms=30_000,
    )
    _session(conn, "s3", "w1", "merge", u=Usage(10, 1, 0.01), wall_ms=1_000)
    _event(conn, "w1", "gate_requested", {"gate": "human_review_approval"}, _at(2))
    _event(conn, "w1", "gate_approved", {"gate": "human_review_approval"}, _at(1.5))
    _event(conn, "w1", "node_completed", {"node_id": "merge"}, _at(1.4))
    _event(conn, "w1", "work_item_completed", {}, _at(1.4))
    _event(conn, "w1", "gate_rejected", {"gate": "plan_approval"}, _at(1.9))
    _event(
        conn,
        "w1",
        "work_item_needs_human",
        {"node_id": "verify", "reason": "needs_context: which repo?"},
        _at(1.8),
    )
    _event(
        conn,
        "w1",
        "work_item_needs_human",
        {
            "node_id": "verify",
            "reason": "verify_fix_loop exhausted after 2 fix cycle(s)",
            "capped": {"cycles": 2, "attempts": 3},
        },
        _at(1.7),
    )

    _item(conn, "w2", repo="/b", template="quick-task", status="needs_human", created=_at(3))
    _session(conn, "s4", "w2", "verify", u=Usage(200, 20, 0.1), wall_ms=10_000)

    # out of the 7d range
    _item(conn, "w3", repo="/a", status="completed", created=_at(40))
    _session(conn, "s5", "w3", "merge", u=Usage(9999, 9999, 99.0), wall_ms=999_000)
    _event(conn, "w3", "node_completed", {"node_id": "merge"}, _at(40))

    # in the previous 8-week window (for completed_prev), out of every other range
    _item(conn, "w4", repo="/a", status="completed", created=_at(70))
    conn.commit()
    return conn


@pytest.fixture
def conn(tmp_path):
    c = _fixture(tmp_path / "orchestrator.db")
    yield c
    c.close()


def test_range_excludes_older_items_entirely(conn):
    seven = analytics.compute(conn, range_="7d", now=NOW)
    assert seven["totals"]["work_items"] == 2
    assert seven["totals"]["tokens_in"] == 1000 + 500 + 10 + 200

    everything = analytics.compute(conn, range_="all", now=NOW)
    assert everything["totals"]["work_items"] == 4
    assert everything["totals"]["mrs_merged"] == 2


def test_totals_group_by_status_and_sum_usage(conn):
    t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    assert t["by_status"] == {"completed": 1, "needs_human": 1}
    assert t["tokens_out"] == 100 + 50 + 1 + 20
    assert t["cost_usd"] == pytest.approx(0.86)
    assert t["capped_out"] == 1
    assert t["wall_ms"] == 60_000 + 30_000 + 1_000 + 10_000
    # w1's verify looped once; w2 never looped
    assert t["rounds"] == 1


def test_a_merge_is_a_completed_node_that_ran_on_merge(conn):
    a = analytics.compute(conn, range_="7d", now=NOW)
    assert a["totals"]["mrs_merged"] == 1
    assert a["weekly_merged"] == [{"week_start": "2026-08-31", "n": 1}]
    repo_a = next(r for r in a["by_repo"] if r["repo"] == "/a")
    assert repo_a["mrs"] == 1


def test_human_wait_is_counted_apart_from_wall_time(conn):
    t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    # gate_requested at -2d, approved at -1.5d
    assert t["human_wait_ms"] == pytest.approx(12 * 3600 * 1000, rel=1e-6)
    assert t["wall_ms"] < t["human_wait_ms"]  # the reviewer, not the agents


def test_by_node_ranks_by_cost_and_averages_its_runs(conn):
    nodes = analytics.compute(conn, range_="7d", now=NOW)["by_node"]
    assert [n["node"] for n in nodes] == ["verify", "merge"]
    verify = nodes[0]
    assert verify["runs"] == 3  # two on w1, one on w2
    assert verify["avg_ms"] == (60_000 + 30_000 + 10_000) // 3
    assert verify["capped_out"] == 1
    # w1 ran rounds 0 and 1; w2 ran round 0 — three distinct (item, round) pairs
    assert verify["rounds"] == 3


def test_repo_and_template_filters_narrow_the_whole_report(conn):
    only_b = analytics.compute(conn, range_="7d", repo="/b", now=NOW)
    assert only_b["totals"]["work_items"] == 1
    assert [r["repo"] for r in only_b["by_repo"]] == ["/b"]
    assert only_b["totals"]["mrs_merged"] == 0

    quick = analytics.compute(conn, range_="7d", template="quick-task", now=NOW)
    assert quick["totals"]["work_items"] == 1

    none = analytics.compute(conn, range_="7d", repo="/nope", now=NOW)
    assert none["totals"]["work_items"] == 0
    assert none["by_node"] == [] and none["weekly_merged"] == []


def test_endpoint_serves_it_and_rejects_a_bad_range(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from support.harness import fake_templates_dir, isolated_bd

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "claude")))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as client:
        body = client.get("/api/analytics?range=30d").json()
        assert set(body) == {
            "totals",
            "weekly_merged",
            "by_node",
            "by_repo",
            "rejected_gates_by_gate",
            "stop_reasons",
        }
        assert body["totals"]["work_items"] == 0
        assert client.get("/api/analytics?range=nope").status_code == 400


def test_an_unpriced_session_makes_the_cost_a_floor_not_a_total(conn):
    """Kraft stores only the cost an agent reported. A session with tokens and no
    cost leaves the sum short, and every level of the report says so."""
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, attempt, created_at, round, tokens_in, tokens_out, "
        "cost_usd, wall_ms) VALUES ('s9', 'w1', 'verify', 'on.verify', 'l', 'r', "
        "'done', 1, ?, 0, 5000, 500, NULL, 1000)",
        (_at(1),),
    )
    conn.commit()

    a = analytics.compute(conn, range_="7d", now=NOW)
    assert a["totals"]["cost_complete"] is False
    assert a["totals"]["tokens_in"] == 1000 + 500 + 10 + 200 + 5000  # tokens still exact
    verify = next(n for n in a["by_node"] if n["node"] == "verify")
    assert verify["cost_complete"] is False
    merge = next(n for n in a["by_node"] if n["node"] == "merge")
    assert merge["cost_complete"] is True
    repo_a = next(r for r in a["by_repo"] if r["repo"] == "/a")
    assert repo_a["cost_complete"] is False


def test_cost_per_item_counts_only_items_that_ran(tmp_path):
    """The Cost tile's subtitle divides money by items. An item that never
    started a node contributed nothing to the numerator, so counting it in the
    denominator understates the average by exactly the size of the backlog."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    _item(conn, "ran", repo="/a", status="completed", created=_at(1))
    _item(conn, "idle1", repo="/a", status="paused", created=_at(1))
    _item(conn, "idle2", repo="/a", status="paused", created=_at(1))
    _session(conn, "s1", "ran", "verify", u=Usage(1000, 100, 4.0))
    conn.commit()
    try:
        t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    finally:
        conn.close()

    assert t["work_items"] == 3, "every item in the range, unchanged"
    assert t["work_items_run"] == 1, "only the one with a worker_sessions row"
    assert t["cost_usd"] == pytest.approx(4.0)
    assert t["cost_usd"] / t["work_items_run"] == pytest.approx(4.0)


def test_work_items_run_is_zero_when_nothing_ran(tmp_path):
    """Zero, not a missing key: the view reads it on every render, including
    the empty-range early return that never reaches the session loop."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    _item(conn, "idle", repo="/a", status="paused", created=_at(1))
    _item(conn, "old", repo="/a", status="completed", created=_at(40))
    _session(conn, "s-old", "old", "verify", u=Usage(1, 1, 9.0))
    conn.commit()
    try:
        in_range = analytics.compute(conn, range_="7d", now=NOW)["totals"]
        # nothing at all inside the range: the early return at analytics.py:116
        empty = analytics.compute(conn, range_="7d", now=NOW + timedelta(days=60))["totals"]
    finally:
        conn.close()

    assert (in_range["work_items"], in_range["work_items_run"]) == (1, 0)
    assert (empty["work_items"], empty["work_items_run"]) == (0, 0)


def test_completed_and_previous_period_delta(conn):
    t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    assert t["completed"] == 1  # w1
    # "all" has no previous window
    assert analytics.compute(conn, range_="all", now=NOW)["totals"]["completed_prev"] is None


def test_median_lead_time_and_human_wait_pct(conn):
    t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    assert t["median_lead_ms"] > 0
    assert 0 <= t["human_wait_pct"] <= 100


def test_fix_cycles_reads_the_verify_node(conn):
    t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    assert t["fix_cycles"] == pytest.approx(
        1.5
    )  # 3 verify rounds / 2 items, from the existing fixture
    assert t["fix_cycles_capped"] == 1


def test_fix_cycles_capped_counts_items_not_sessions(tmp_path):
    """mark_sessions_capped_out flags every measuring task in the node (verify
    has on.test.run + on.review.local.run), so one capped item leaves two
    capped_out sessions. fix_cycles_capped must still read 1."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    _item(conn, "w1", created=_at(1))
    _session(conn, "s1", "w1", "verify", round=0, status="capped_out")
    _session(conn, "s2", "w1", "verify", round=0, status="capped_out")
    conn.commit()
    try:
        t = analytics.compute(conn, range_="7d", now=NOW)["totals"]
    finally:
        conn.close()
    assert t["fix_cycles_capped"] == 1


def test_rejected_gates_and_stop_reasons_come_from_events(conn):
    a = analytics.compute(conn, range_="7d", now=NOW)
    assert a["totals"]["rejected_gates"] >= 1
    assert {"gate": "plan_approval", "n": 1} in a["rejected_gates_by_gate"]
    labels = {s["label"] for s in a["stop_reasons"]}
    assert "agent question · needs_context" in labels
    assert any(label.startswith("capped out · verify_fix_loop") for label in labels)


def test_by_repo_done_and_cycles(conn):
    repo_a = next(
        r for r in analytics.compute(conn, range_="7d", now=NOW)["by_repo"] if r["repo"] == "/a"
    )
    assert repo_a["done"] == 1  # w1 is completed
    assert repo_a["cycles"] == pytest.approx(1.0)  # w1's own max round is 1
