"""The board's daily spend total (B10), events paging and the run summary
(B13). A sibling of test_board.py."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from support.harness import v1_chain

from kraft import store

from .test_board import _append, _seed_events, _seed_repo, _seed_session
from .test_board_stop import _paused_item, _run

# ── daily total (B10) ───────────────────────────────────────────────────────


def _session_with_cost(wid: str, *, cost_usd: float, created_at: str) -> None:
    """A worker_sessions row with just enough to be counted by `budget_spend`
    -- not a real dispatch, so a test can control `created_at` and `cost_usd`
    directly rather than racing a fake agent for them."""
    _run(
        lambda c: store.create_session(
            c,
            id=f"s-{created_at}",
            work_item_id=wid,
            node_id="n",
            hook_point="n.main.n",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(
        lambda c: c.execute(
            "UPDATE worker_sessions SET cost_usd = ?, created_at = ? WHERE id = ?",
            (cost_usd, created_at, f"s-{created_at}"),
        )
    )


def test_budget_cap_daily_counts_only_since_local_midnight(client, repo):
    wid = _paused_item(client, repo)
    midnight = store.local_midnight_utc()
    yesterday = "2020-01-01T00:00:00+00:00"
    assert yesterday < midnight
    _session_with_cost(wid, cost_usd=2.0, created_at=midnight)
    _session_with_cost(wid, cost_usd=99.0, created_at=yesterday)

    daily = client.get(f"/api/work-items/{wid}").json()["budget_cap"]["daily"]
    assert daily["spent_usd"] == 2.0


def test_budget_today_with_and_without_daily_cap(client, repo):
    wid = _paused_item(client, repo)
    _session_with_cost(wid, cost_usd=3.0, created_at=store.local_midnight_utc())

    body = client.get("/api/budget/today").json()
    assert body == {"spent_usd": 3.0, "cap_usd": 50.0}  # templates/policy.yaml's shipped default


@pytest.mark.api_client(edit_templates=lambda tdir: _set_daily_usd(tdir, None))
def test_budget_today_with_no_daily_cap_configured(client):
    assert client.get("/api/budget/today").json()["cap_usd"] is None


def _set_daily_usd(templates_dir, value) -> None:
    import yaml

    path = templates_dir / "policy.yaml"
    data = yaml.safe_load(path.read_text())
    data.setdefault("budget", {})["daily_usd"] = value
    path.write_text(yaml.safe_dump(data))


# ── events paging and the run summary (B13) ────────────────────────────────


def test_events_paging_before_seq_and_limit(client, repo):
    """`before_seq` + `limit`: the last `limit` events with `seq < before_seq`,
    oldest first (H.3)."""
    wid = _paused_item(client, repo)
    seqs = [_append(wid, "x", {"i": i}) for i in range(5)]

    page = client.get(f"/api/work-items/{wid}/events", params={"before_seq": seqs[4], "limit": 2})
    assert page.status_code == 200
    assert [e["seq"] for e in page.json()] == seqs[2:4]


def test_events_paging_both_cursors_is_422(client, repo):
    wid = _paused_item(client, repo)
    resp = client.get(f"/api/work-items/{wid}/events", params={"after_seq": 0, "before_seq": 10})
    assert resp.status_code == 422


def test_events_paging_limit_out_of_range_is_422(client, repo):
    wid = _paused_item(client, repo)
    assert client.get(f"/api/work-items/{wid}/events", params={"limit": 0}).status_code == 422
    assert client.get(f"/api/work-items/{wid}/events", params={"limit": 501}).status_code == 422


def _two_gate_chain(repo):
    return v1_chain(
        [
            {
                "id": "a",
                "kind": "exec",
                "tasks": [{"id": "t1", "kind": "agent", "harness": "fake", "prompt": "go"}],
            },
            {"id": "gate1", "kind": "gate"},
            {
                "id": "b",
                "kind": "exec",
                "steps": [
                    {
                        "id": "s1",
                        "tasks": [{"id": "t1", "kind": "agent", "harness": "fake", "prompt": "go"}],
                    },
                    {
                        "id": "s2",
                        "tasks": [{"id": "t2", "kind": "agent", "harness": "fake", "prompt": "go"}],
                    },
                ],
            },
            {"id": "gate2", "kind": "gate"},
        ],
        repo=repo,
    )


def test_summary_counts_nodes_gates_and_the_current_step(client, repo):
    """H.4: `nodes_done`/`nodes_total` from the frozen chain, `gates_passed`
    among the completed nodes, and the current node's `step` when it declares
    more than one -- read from its latest non-escalation session's task."""
    chain = _two_gate_chain(repo)
    wid = _paused_item(client, repo, materialized_chain=chain.to_json())
    _run(lambda c: store.load_chain(c, wid, "a"))
    _run(lambda c: store.complete_node(c, wid, "a"))
    _run(lambda c: store.enter_node(c, wid, "gate1"))
    _run(lambda c: store.complete_node(c, wid, "gate1"))
    _run(lambda c: store.enter_node(c, wid, "b"))
    _run(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="b",
            hook_point="b.s2.t2",
            log_path="/l",
            result_path="/r",
        )
    )

    summary = client.get(f"/api/work-items/{wid}").json()["summary"]
    assert summary == {
        "nodes_done": 2,
        "nodes_total": 4,
        "gates_passed": 1,
        "step": {"index": 2, "count": 2},
    }


def test_summary_step_is_null_for_a_single_step_node(client, repo):
    chain = _two_gate_chain(repo)
    wid = _paused_item(client, repo, materialized_chain=chain.to_json())
    _run(lambda c: store.load_chain(c, wid, "a"))

    summary = client.get(f"/api/work-items/{wid}").json()["summary"]
    assert summary["step"] is None


# ── the list row's step and mr_ref ──────────────────────────────────────────


def test_the_list_row_carries_mr_ref_like_the_detail(client, repo):
    """The board draws "merged !N" from the list, so the row must name the same
    merge request the detail does, single-repo (latest `mr_opened`) and
    multi-repo (the root row's own) alike."""

    def new():
        return client.post(
            "/api/work-items",
            json={
                "repo": str(repo),
                "title": "t",
                "chain_template": "quick-task",
                "autostart": False,
            },
        ).json()["id"]

    def listed(wid):
        return next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)

    single, multi = new(), new()
    assert listed(single)["mr_ref"] is None
    for url in ("https://forge.example/mr/12", "https://forge.example/mr/12?refresh"):
        _seed_events(client, single, [{"number": 12, "url": url}], event_type="mr_opened")
    assert listed(single)["mr_ref"] == {"number": 12, "url": "https://forge.example/mr/12?refresh"}

    _seed_repo(
        client,
        multi,
        repo_path="/wt/pkg",
        role="submodule",
        merge_rank=0,
        mr_ref={"number": 1, "url": "https://forge.example/mr/1"},
    )
    _seed_repo(
        client,
        multi,
        repo_path="/wt",
        role="root",
        merge_rank=1,
        mr_ref={"number": 2, "url": "https://forge.example/mr/2"},
    )
    _seed_events(
        client, multi, [{"number": 3, "url": "https://forge.example/mr/3"}], event_type="mr_opened"
    )
    assert (
        listed(multi)["mr_ref"]["number"]
        == 2
        == client.get(f"/api/work-items/{multi}").json()["mr_ref"]["number"]
    )


def test_the_list_row_carries_the_current_step_and_its_task(client, repo):
    """`default`'s verification node has two steps (tests, review). The row's
    `step` is the step of the node's latest non-escalation session; an item
    with no session yet, or on a single-step node, has none."""
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "t", "chain_template": "default", "autostart": False},
    ).json()["id"]
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.execute("UPDATE work_items SET current_node_id = 'verification' WHERE id = ?", (wid,))
    conn.commit()
    conn.close()

    def step():
        return next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)[
            "step"
        ]

    assert step() is None
    _seed_session(
        client,
        wid,
        session_id="a",
        hook_point="verification.tests.test_changed_scopes",
        node_id="verification",
    )
    assert step() == {"index": 1, "count": 2, "name": "tests", "task": "test_changed_scopes"}
    _seed_session(
        client,
        wid,
        session_id="b",
        hook_point="verification.review.code_review",
        node_id="verification",
    )
    _seed_session(client, wid, session_id="c", hook_point="escalation", node_id="verification")
    assert step() == {"index": 2, "count": 2, "name": "review", "task": "code_review"}
    assert client.get(f"/api/work-items/{wid}").json()["summary"]["step"] == {
        "index": 2,
        "count": 2,
    }


def test_step_of_a_task_path_without_step_and_task_segments_has_no_name():
    """One hand-written chain with a short task path must not 500 the whole
    list: the row gets the position, without a name or task."""
    from kraft.api.routes.board import _step_of

    chain = {"nodes": [{"id": "verify", "steps": [["verify.a.b"], ["verify.c"]]}]}
    row = {"current_node_id": "verify"}
    assert _step_of(row, chain, "verify.c", with_task=True) == {"index": 2, "count": 2}
    assert _step_of(row, chain, "verify.a.b", with_task=True) == {
        "index": 1,
        "count": 2,
        "name": "a",
        "task": "b",
    }
