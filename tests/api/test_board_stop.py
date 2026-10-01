"""The board's `display_status` (B.1) and `stop` detail (B.3/B.4), including
B6's rate-limit fallback facts. A sibling of test_board.py."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from support.harness import v1_chain

from kraft import events, store
from kraft import policy as policy_mod
from kraft.api.routes import board

# ── display_status (B.1) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("row", "stop_kind", "escalated", "pending_gate", "expected"),
    [
        # archived wins over everything, even a needs_human row that would
        # otherwise read as escalated or failed (rule 1 order).
        pytest.param(
            {"archived_at": "t", "status": "needs_human"},
            "failed",
            True,
            "g",
            "archived",
            id="archived-wins",
        ),
        pytest.param(
            {"archived_at": None, "status": "completed"}, None, False, None, "done", id="done"
        ),
        pytest.param(
            {"archived_at": None, "status": "abandoned"},
            None,
            False,
            None,
            "cancelled",
            id="cancelled",
        ),
        pytest.param(
            {"archived_at": None, "status": "paused"}, None, False, None, "paused", id="paused"
        ),
        pytest.param(
            {"archived_at": None, "status": "active"}, None, False, None, "running", id="running"
        ),
        pytest.param(
            {"archived_at": None, "status": "waiting"},
            None,
            False,
            None,
            "waiting",
            id="waiting-status",
        ),
        pytest.param(
            {"archived_at": None, "status": "rate_limited"},
            None,
            False,
            None,
            "waiting",
            id="rate-limited-status",
        ),
        # A pending gate wins over an escalation and a failing kind both.
        pytest.param(
            {"archived_at": None, "status": "needs_human"},
            "infra",
            True,
            "code_review",
            "needs_you",
            id="gate-wins",
        ),
        # An escalation wins over the kind grouping.
        pytest.param(
            {"archived_at": None, "status": "needs_human"},
            "infra",
            True,
            None,
            "escalated",
            id="escalated-wins",
        ),
        *[
            pytest.param(
                {"archived_at": None, "status": "needs_human"},
                k,
                False,
                None,
                "failed",
                id=f"kind-{k}",
            )
            for k in ("failed", "config", "infra")
        ],
        *[
            pytest.param(
                {"archived_at": None, "status": "needs_human"},
                k,
                False,
                None,
                "needs_you",
                id=f"kind-{k}",
            )
            for k in ("question", "cap", "budget", "conflict", "mr_closed", "stuck")
        ],
        pytest.param(
            {"archived_at": None, "status": "needs_human"},
            None,
            False,
            None,
            "needs_you",
            id="kind-null",
        ),
    ],
)
def test_display_status_branches(row, stop_kind, escalated, pending_gate, expected):
    """One case per branch of B.1 rule 1, plus the three ordering cases
    (archived-wins, gate-wins, escalated-wins) that prove the `if`s are
    checked in the stated order rather than independently."""
    assert board.display_status(row, stop_kind, escalated, pending_gate) == expected


# ── stop (B.3/B.4) and B6's rate-limit facts ───────────────────────────────


def _run(fn) -> None:
    """Run a store writer directly against the orchestrator db -- like
    `_append`, but for a writer that needs more than one statement
    (`mark_needs_human`, `create_session`, ...) committed together."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        fn(conn)
        conn.commit()
    finally:
        conn.close()


def _paused_item(client, repo, *, materialized_chain: str | None = None) -> str:
    """A work item with no chain run yet -- `store.create_work_item` directly,
    like the API's own create route, so a test can drive it onto a stop by
    hand rather than through a real walk."""
    wid = "w1"
    _run(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition="{}",
            status="paused",
            materialized_chain=materialized_chain,
        )
    )
    return wid


def test_stop_detail_needs_human(client, repo):
    """`stop` on a `needs_human` detail: `task`/`attempt` from the node's
    latest non-escalation session, `resume_at` null (no `retry_at` on a
    needs_human stop), `reason` and `facts` from the event."""
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="implement",
            hook_point="implement.main.implement",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(
        lambda c: store.create_session(
            c,
            id="s2",
            work_item_id=wid,
            node_id="implement",
            hook_point="implement.main.implement",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(lambda c: store.mark_needs_human(c, wid, "implement", "task failed", kind="failed"))

    body = client.get(f"/api/work-items/{wid}").json()
    assert body["display_status"] == "failed"
    assert body["stop"] == {
        "kind": "failed",
        "node": "implement",
        "task": "implement.main.implement",
        "attempt": 2,
        "resume_at": None,
        "reason": "task failed",
        "facts": {},
    }


def test_stop_detail_waiting(client, repo):
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "ci_poll"))
    _run(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="ci_poll",
            hook_point="ci_poll.main.poll",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(lambda c: store.mark_waiting(c, wid, "ci_poll", "2026-10-01T00:10:00+00:00"))

    body = client.get(f"/api/work-items/{wid}").json()
    assert body["display_status"] == "waiting"
    assert body["stop"] == {
        "kind": "wait",
        "node": "ci_poll",
        "task": "ci_poll.main.poll",
        "attempt": 1,
        "resume_at": "2026-10-01T00:10:00+00:00",
        "reason": None,
        "facts": {},
    }
    assert body["retry_at"] == body["stop"]["resume_at"]


def test_stop_detail_rate_limited(client, repo):
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="implement",
            hook_point="implement.main.implement",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(lambda c: store.mark_rate_limited(c, wid, "implement", "2026-10-01T00:05:00+00:00"))

    body = client.get(f"/api/work-items/{wid}").json()
    assert body["display_status"] == "waiting"
    stop = body["stop"]
    assert stop["kind"] == "rate_limit"
    assert stop["node"] == "implement"
    assert stop["task"] == "implement.main.implement"
    assert stop["attempt"] == 1
    assert stop["resume_at"] == "2026-10-01T00:05:00+00:00" == body["retry_at"]
    assert stop["facts"]["retries"] == {"count": 0, "cap": 5}
    assert "fallback" not in stop["facts"]  # no AgentTask to resolve: no chain was loaded


def _stopped_item(client, repo) -> str:
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(lambda c: store.mark_needs_human(c, wid, "implement", "task failed", kind="failed"))
    return wid


def _escalation_turn(wid: str, session: str | None, status: str = "pending") -> None:
    """An `escalation_message` after the stop, and the turn's session (none
    yet when `session` is None) in `status`."""
    _run(lambda c: events.append(c, wid, "escalation_message", {"session_id": "e1", "thread": 1}))
    if session is None:
        return
    _run(
        lambda c: store.create_session(
            c,
            id="e1",
            work_item_id=wid,
            node_id="implement",
            hook_point="escalation",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(lambda c: c.execute("UPDATE worker_sessions SET status = ? WHERE id = 'e1'", (status,)))


def _displays(client, wid: str) -> tuple[str, str]:
    """`display_status` on the detail and on the list: they must agree."""
    detail = client.get(f"/api/work-items/{wid}").json()["display_status"]
    row = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
    return detail, row["display_status"]


@pytest.mark.parametrize("status", ["pending", "running"])
def test_display_status_escalated_while_the_turn_is_working(client, repo, status):
    """An `escalation_message` after the stop turns it `escalated` -- even a
    kind that would otherwise read as `failed` (B.1 rule 1.5's check runs first)
    -- for as long as the turn's session is pending or running."""
    wid = _stopped_item(client, repo)
    _escalation_turn(wid, "e1", status)

    assert _displays(client, wid) == ("escalated", "escalated")


@pytest.mark.parametrize("status", ["failed", "done", "capped_out", "needs_context"])
def test_display_status_is_the_stops_own_once_the_escalation_turn_has_exited(client, repo, status):
    """An escalation whose agent has exited leaves nothing running: the item is
    back with the person, under the stop's own status, so it offers Retry
    (Kraft-9d8b2.48)."""
    wid = _stopped_item(client, repo)
    _escalation_turn(wid, "e1", status)

    assert _displays(client, wid) == ("failed", "failed")


def test_a_turn_with_no_session_row_yet_reads_escalated_only_while_it_is_new(client, repo):
    """The message is recorded before the turn launches, and the session row
    follows: a fresh message with no row is a turn starting; an old one is not."""
    wid = _stopped_item(client, repo)
    _escalation_turn(wid, None)
    assert _displays(client, wid) == ("escalated", "escalated")

    _run(
        lambda c: c.execute(
            "UPDATE events SET created_at = '2020-01-01T00:00:00+00:00' "
            "WHERE type = 'escalation_message'"
        )
    )
    assert _displays(client, wid) == ("failed", "failed")


def test_display_status_escalation_before_stop_does_not_count(client, repo):
    """An `escalation_message` from an *earlier*, already-superseded stop must
    not make a fresh stop read as escalated."""
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(lambda c: events.append(c, wid, "escalation_message", {"session_id": "stale"}))
    _run(lambda c: store.mark_needs_human(c, wid, "implement", "task failed", kind="failed"))

    assert client.get(f"/api/work-items/{wid}").json()["display_status"] == "failed"


def test_list_display_status_and_stop_without_facts(client, repo):
    """The list carries `display_status` and a `stop` without `facts`,
    `task` or `attempt` -- it reads no sessions per row (B.4)."""
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="implement",
            hook_point="implement.main.implement",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(lambda c: store.mark_needs_human(c, wid, "implement", "task failed", kind="failed"))

    row = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
    assert row["display_status"] == "failed"
    assert row["stop"] == {
        "kind": "failed",
        "node": "implement",
        "resume_at": None,
        "reason": "task failed",
    }


def test_rate_limit_fallback_facts(client, repo):
    """B6: a rate-limited stop's `facts.fallback` is the stopped task's
    declared fallback list, and `fallback_allowed` the subset its resolved
    policy still permits -- the item's own policy override here, narrowed
    *after* the chain materialized (`MaterializedChain.policy_for`, the same
    resolution `fallback-never-escapes-allowed-harnesses` checks at lint)."""
    nodes = [
        {
            "id": "implement",
            "kind": "exec",
            "tasks": [
                {
                    "id": "implement",
                    "kind": "agent",
                    "harness": "codex",
                    "prompt": "Implement it.",
                    "fallback": [{"harness": "claude"}, {"harness": "gemini"}],
                }
            ],
        }
    ]
    chain = v1_chain(nodes, repo=repo)
    wid = _paused_item(client, repo, materialized_chain=chain.to_json())
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="implement",
            hook_point="implement.main.implement",
            log_path="/l",
            result_path="/r",
        )
    )
    _run(lambda c: store.mark_rate_limited(c, wid, "implement", "2026-10-01T00:05:00+00:00"))
    override = policy_mod.WorkItemPolicy.model_validate({"allowed_harnesses": ["codex", "claude"]})
    _run(lambda c: store.set_policy_override(c, wid, override))

    facts = client.get(f"/api/work-items/{wid}").json()["stop"]["facts"]
    assert facts["fallback"] == ["claude", "gemini"]
    assert facts["fallback_allowed"] == ["claude"]


@pytest.mark.parametrize(
    ("status", "node", "expected"),
    [("waiting", "ci_poll", "wait"), ("rate_limited", "implement", "rate_limit")],
)
def test_stop_kind_falls_back_on_a_pre_migration_row(client, repo, status, node, expected):
    """A `waiting`/`rate_limited` row stopped before migration 44 (B1) carries
    no `stop_kind`; `stop.kind` on both the list and the detail falls back to
    what the status itself says."""
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, node))
    if status == "waiting":
        _run(lambda c: store.mark_waiting(c, wid, node, "2026-10-01T00:10:00+00:00"))
    else:
        _run(lambda c: store.mark_rate_limited(c, wid, node, "2026-10-01T00:10:00+00:00"))
    _run(lambda c: c.execute("UPDATE work_items SET stop_kind = NULL WHERE id = ?", (wid,)))

    assert client.get(f"/api/work-items/{wid}").json()["stop"]["kind"] == expected
    row = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
    assert row["stop"]["kind"] == expected


@pytest.mark.parametrize(
    ("reason", "extra", "kind", "display"),
    [
        ("task failed in node implementation: implement [agent]", {}, "failed", "failed"),
        ("needs_context: which schema?", {}, "question", "needs_you"),
        (
            "verify.fix_loop exhausted after 3 fix cycle(s)",
            {"capped": {"cycles": 3, "attempts": 3}},
            "cap",
            "needs_you",
        ),
        (
            "budget cap reached",
            {"budget": {"scope": "usd", "spent_usd": 5.0, "cap_usd": 5.0}},
            "budget",
            "needs_you",
        ),
    ],
)
def test_a_needs_human_row_with_no_stop_kind_is_read_off_its_stop_event(
    client, repo, reason, extra, kind, display
):
    """A `needs_human` row stopped on 1.4.0 has no `stop_kind` (and its event no
    `kind`). It is never called a rate limit: the kind comes off the event, and
    `display_status` follows it, so a failed task reads `failed` on the list and
    the detail (Kraft-9d8b2.49)."""
    wid = _paused_item(client, repo)
    _run(lambda c: store.load_chain(c, wid, "implement"))
    _run(lambda c: store.mark_needs_human(c, wid, "implement", reason, kind="failed", **extra))
    _run(lambda c: c.execute("UPDATE work_items SET stop_kind = NULL WHERE id = ?", (wid,)))
    _run(
        lambda c: c.execute(
            "UPDATE events SET payload = json_remove(payload, '$.kind') "
            "WHERE type = 'work_item_needs_human'"
        )
    )

    detail = client.get(f"/api/work-items/{wid}").json()
    row = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
    assert (detail["stop"]["kind"], row["stop"]["kind"]) == (kind, kind)
    assert (detail["display_status"], row["display_status"]) == (display, display)


def _capped_item(client, repo, maxima: dict, limit: dict | None) -> dict:
    from support.harness import v1_resolved

    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import WorkItemTarget

    nodes = [
        {
            "id": "implement",
            "kind": "exec",
            "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
        }
    ]
    chain = v1_resolved(nodes).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(
            InstancePolicyInput.model_validate({"maxima": maxima})
        ),
    )
    wid = _paused_item(client, repo, materialized_chain=chain.to_json())
    _run(lambda c: store.mark_needs_human(c, wid, "implement", "capped", kind="cap", limit=limit))
    return client.get(f"/api/work-items/{wid}").json()["stop"]


@pytest.mark.parametrize(
    ("maxima", "limit", "maximum"),
    [
        pytest.param(
            {"max_attempts": 7},
            {"path": "implement", "key": "max_attempts", "value": 3},
            7,
            id="fix-loop-attempts",
        ),
        pytest.param(
            {"timeout_minutes": 90},
            {"path": "implement", "key": "timeout_minutes", "value": 30},
            90,
            id="fix-loop-wall-clock",
        ),
        pytest.param(
            {"work_item": {"time_cap_minutes": 300}},
            {"path": "", "key": "time_cap_minutes", "value": 60},
            300,
            id="work-item-cap",
        ),
        pytest.param(
            {"work_item": {"time_cap_minutes": 300}, "tasks": {"time_cap_minutes": 100}},
            {"path": "", "key": "time_cap_minutes", "value": 60},
            300,
            id="a-narrower-levels-maximum-does-not-bound-the-work-item",
        ),
        pytest.param(
            {}, {"path": "", "key": "total_time_cap_minutes", "value": 60}, None, id="no-maximum"
        ),
        pytest.param(
            {"work_item": {"budget_usd": 25}},
            {"path": "", "key": "budget_usd", "value": 5.0},
            25,
            id="budget-usd",
        ),
    ],
)
def test_a_cap_stop_names_the_limit_that_raises_it_with_the_administrator_maximum(
    client, repo, maxima, limit, maximum
):
    assert _capped_item(client, repo, maxima, limit)["limit"] == {**limit, "maximum": maximum}


def test_a_cap_stop_with_no_raisable_limit_has_no_limit_key(client, repo):
    assert "limit" not in _capped_item(client, repo, {}, None)
