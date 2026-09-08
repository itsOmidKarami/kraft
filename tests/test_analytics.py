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

    _item(conn, "w2", repo="/b", template="quick-task", status="needs_human", created=_at(3))
    _session(conn, "s4", "w2", "verify", u=Usage(200, 20, 0.1), wall_ms=10_000)

    # out of the 7d range
    _item(conn, "w3", repo="/a", status="completed", created=_at(40))
    _session(conn, "s5", "w3", "merge", u=Usage(9999, 9999, 99.0), wall_ms=999_000)
    _event(conn, "w3", "node_completed", {"node_id": "merge"}, _at(40))
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
    assert everything["totals"]["work_items"] == 3
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
        body = client.get("/analytics?range=30d").json()
        assert set(body) == {"totals", "weekly_merged", "by_node", "by_repo"}
        assert body["totals"]["work_items"] == 0
        assert client.get("/analytics?range=nope").status_code == 400


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
