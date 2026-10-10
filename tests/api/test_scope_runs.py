"""`scope_runs` on the item detail: every command a changed-test-scope task ran, over every round
and repository, and the ones its rounds picked and have not started."""

from __future__ import annotations

import pytest

from kraft import events, store

from .test_board_stop import _exit_session, _paused_item, _run, _verified_item


def test_detail_scope_runs_lists_every_round_with_its_repository_and_scope(client, repo, tmp_path):
    """`scope_runs`: every command the changed-test-scope task ran, over every
    round and repository, in the order it started -- not the latest run alone
    (`test_result`) -- each with the scope the repo's table names it by, where
    the table lists it (an area's setup half a place before its scopes), and
    `passed` None while it has not finished."""
    area = {
        "web": {
            "paths": ["web/**"],
            "setup": "npm ci",
            "verification": {"test_scopes": [{"paths": ["web/**"], "command": "web-test"}]},
        }
    }
    runs = [
        ("fe-cmd", "failed", 1, 0, "ws"),
        ("be-cmd", "done", 0, 0, "ws"),
        ("fe-cmd", "done", 0, 1, "ws"),
        ("docs-cmd", "running", 0, 1, "pkg"),
        ("npm ci", "done", 0, 1, "pkg"),
        ("gone-cmd", "done", 0, 1, "pkg"),
    ]
    wid = _verified_item(client, repo, tmp_path, runs, areas=area)

    got = client.get(f"/api/work-items/{wid}").json()["scope_runs"]

    assert [
        (
            r["session_id"],
            r["repository"],
            r["round"],
            r.get("scope"),
            r["passed"],
            r.get("exit_code"),
        )
        for r in got
    ] == [
        ("s0", "ws", 0, "frontend/**", False, 1),
        ("s1", "ws", 0, "backend/**", True, 0),
        ("s2", "ws", 1, "frontend/**", True, 0),
        ("s3", "pkg", 1, "docs/**", None, 0),
        ("s4", "pkg", 1, None, True, 0),
        # A command the table no longer declares names no scope.
        ("s5", "pkg", 1, None, True, 0),
    ]
    assert {(r["node_id"], r["hook_point"]) for r in got} == {("verify", "verify.main.t")}
    assert [(r.get("order"), r.get("setup"), r.get("area")) for r in got] == [
        (0, None, None),
        (1, None, None),
        (0, None, None),
        (2, None, None),
        (2.5, True, "web"),
        (None, None, None),
    ]


def test_detail_scope_runs_is_empty_without_a_changed_test_scope_task(client, repo):
    wid = _paused_item(client, repo)
    assert client.get(f"/api/work-items/{wid}").json()["scope_runs"] == []


def _picks(wid, repository, rnd, commands) -> None:
    """A dispatch's picks, as the builtin records them before it runs any scope."""
    _run(
        lambda c: events.append(
            c,
            wid,
            "test_scopes_selected",
            {
                "node_id": "verify",
                "hook_point": "verify.main.t",
                "repository": repository,
                "round": rnd,
                "commands": commands,
            },
        )
    )


def _runs(client, wid) -> list[tuple]:
    got = client.get(f"/api/work-items/{wid}").json()["scope_runs"]
    return [
        (r["session_id"], r["repository"], r["round"], r["command"], r["passed"], r.get("pending"))
        for r in got
    ]


def _session(tmp_path, wid, sid, command, status, rnd=0, repository="ws") -> None:
    _exit_session(wid, sid, tmp_path / f"{sid}.json", command, status, 0, rnd, repository)


def test_detail_scope_runs_lists_what_a_round_picked_and_has_not_started(client, repo, tmp_path):
    """`test_scopes_selected` names the commands a round will run in a repository before it runs
    any: the ones with no session yet come last as `pending`, with no session, and every entry of
    a round that recorded its picks is `selected`, so what it dropped is known before it ends."""
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 1, ["fe-cmd", "be-cmd", "docs-cmd"])
    _picks(wid, "pkg", 1, ["be-cmd"])
    _session(tmp_path, wid, "s0", "fe-cmd", "done", 1)
    _session(tmp_path, wid, "s1", "be-cmd", "running", 1)

    got = client.get(f"/api/work-items/{wid}").json()["scope_runs"]

    assert [
        (
            r["session_id"],
            r["repository"],
            r["command"],
            r["passed"],
            r.get("pending"),
            r.get("selected"),
        )
        for r in got
    ] == [
        ("s0", "ws", "fe-cmd", True, None, True),
        ("s1", "ws", "be-cmd", None, None, True),
        (None, "ws", "docs-cmd", None, True, True),
        (None, "pkg", "be-cmd", None, True, True),
    ]
    # A pending command is named by the table as any other is.
    assert [(r["command"], r.get("scope"), r.get("order")) for r in got[2:]] == [
        ("docs-cmd", "docs/**", 2),
        ("be-cmd", "backend/**", 1),
    ]


def test_detail_scope_runs_leaves_no_command_pending_once_the_walk_has_moved_on(
    client, repo, tmp_path
):
    """An area's setup that fails, or a stop, ends the loop with commands picked and never started:
    once the walk is no longer on the node they are not pending, or they read "waiting" for good."""
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 0, ["fe-cmd", "be-cmd"])
    _session(tmp_path, wid, "s0", "fe-cmd", "failed")
    assert _runs(client, wid) == [
        ("s0", "ws", 0, "fe-cmd", False, None),
        (None, "ws", 0, "be-cmd", None, True),
    ]

    _run(
        lambda c: c.execute(
            "UPDATE work_items SET current_node_id = 'elsewhere' WHERE id = ?", (wid,)
        )
    )

    assert _runs(client, wid) == [("s0", "ws", 0, "fe-cmd", False, None)]


def test_detail_scope_runs_leaves_nothing_pending_in_a_round_the_loop_went_on_past_or_a_stop_ended(
    client, repo, tmp_path
):
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 0, ["fe-cmd", "be-cmd"])
    _session(tmp_path, wid, "s0", "fe-cmd", "failed")
    _picks(wid, "ws", 1, ["fe-cmd"])
    # Round 1 is the newest, so it alone can still start what it picked.
    assert _runs(client, wid) == [
        ("s0", "ws", 0, "fe-cmd", False, None),
        (None, "ws", 1, "fe-cmd", None, True),
    ]

    # A stop leaves the walk's place on the node but ends the loop.
    _run(lambda c: c.execute("UPDATE work_items SET status = 'needs_human' WHERE id = ?", (wid,)))

    assert _runs(client, wid) == [("s0", "ws", 0, "fe-cmd", False, None)]


def test_detail_scope_runs_pends_nothing_in_a_repository_a_later_round_never_reached(
    client, repo, tmp_path
):
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 0, ["fe-cmd"])
    _picks(wid, "pkg", 0, ["be-cmd"])
    _picks(wid, "ws", 1, ["fe-cmd"])
    # Round 1 reached `ws` and stopped there: `pkg`'s round 0 has moved on with it.
    assert [r[1:4] for r in _runs(client, wid)] == [("ws", 1, "fe-cmd")]


def test_detail_scope_runs_pends_nothing_a_recovery_re_measure_ran_past(client, repo, tmp_path):
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 0, ["fe-cmd", "be-cmd"])
    _session(tmp_path, wid, "s0", "fe-cmd", "failed")
    # The re-measure after `on_failure` picks at round -1 (`walk._REPAIR_ROUND`).
    _picks(wid, "ws", -1, ["be-cmd"])
    _session(tmp_path, wid, "s1", "be-cmd", "done", rnd=-1)
    assert _runs(client, wid) == [
        ("s0", "ws", 0, "fe-cmd", False, None),
        ("s1", "ws", -1, "be-cmd", True, None),
    ]


def test_detail_scope_runs_calls_every_way_of_not_finishing_a_pass_a_fail(client, repo, tmp_path):
    """`passed` is None only while a command has not finished: a capped or refused one has."""
    wid = _verified_item(client, repo, tmp_path, [])
    for i, status in enumerate(
        ["done", "failed", "capped_out", "config_error", "unknown", "running", "pending"]
    ):
        _session(tmp_path, wid, f"s{i}", "fe-cmd", status, i)

    assert [r[4] for r in _runs(client, wid)] == [True, False, False, False, False, None, None]


def test_detail_scope_runs_counts_only_the_latest_dispatch_of_a_repository_and_round(
    client, repo, tmp_path
):
    """A retry re-runs round 0: its picks replace the first pass's, and a command the first pass ran
    that the retry did not pick is not part of it (nor is the one it ran and the retry picked
    again shown as that pass's result)."""
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 0, ["fe-cmd", "be-cmd"])
    _session(tmp_path, wid, "s0", "fe-cmd", "failed")
    _session(tmp_path, wid, "s1", "be-cmd", "done")
    _picks(wid, "ws", 0, ["fe-cmd", "docs-cmd"])
    _session(tmp_path, wid, "s2", "fe-cmd", "running")

    assert _runs(client, wid) == [
        ("s2", "ws", 0, "fe-cmd", None, None),
        (None, "ws", 0, "docs-cmd", None, True),
    ]


@pytest.mark.parametrize(
    "again, why",
    [
        pytest.param(0, {}, id="its-rounds-restarted"),
        pytest.param(
            1, {"reason": "reject", "gate": "g"}, id="rejected-back-at-the-round-it-left-off"
        ),
    ],
)
def test_detail_keeps_each_pass_of_a_node_under_its_own_number(client, repo, tmp_path, again, why):
    """A node the chain ran again is on a new pass, which counts its rounds on its own: the pass
    before keeps its rounds and its picks, under its number, and is not folded into the new one
    (a re-run after a gate reject resumed at the round it left off at and read as one more attempt
    of that round, Kraft-9d8b2.150). Each session says which pass it ran in, and the node what
    started each."""
    wid = _verified_item(client, repo, tmp_path, [])
    for rnd in (0, 1):
        _picks(wid, "ws", rnd, ["fe-cmd"])
        _session(tmp_path, wid, f"old{rnd}", "fe-cmd", "failed", rnd)
    if why:
        _run(lambda c: events.append(c, wid, "gate_rejected", {"gate": "g", "node": "verify"}))
    _picks(wid, "ws", again, ["fe-cmd", "be-cmd"])
    _session(tmp_path, wid, "new", "fe-cmd", "running", again)

    detail = client.get(f"/api/work-items/{wid}").json()

    assert [
        (r["pass"], r["session_id"], r["round"], r["command"], r.get("pending"))
        for r in detail["scope_runs"]
    ] == [
        (1, "old0", 0, "fe-cmd", None),
        (1, "old1", 1, "fe-cmd", None),
        (2, "new", again, "fe-cmd", None),
        (2, None, again, "be-cmd", True),
    ]
    assert {s["id"]: s["pass"] for s in detail["worker_sessions"]} == {
        "old0": 1,
        "old1": 1,
        "new": 2,
    }
    assert detail["node_passes"] == {"verify": [{"pass": 1}, {"pass": 2, **why}]}


def test_detail_scope_runs_gives_picks_nothing_has_started_from_to_the_nodes_newest_pass(
    client, repo, tmp_path
):
    """The node is on its second pass and the scope task has picked and started nothing in it yet:
    the picks are that pass's, and do not stand in for the first pass's round of the same number."""
    wid = _verified_item(client, repo, tmp_path, [])
    _picks(wid, "ws", 0, ["fe-cmd"])
    _session(tmp_path, wid, "old", "fe-cmd", "failed")
    _run(lambda c: events.append(c, wid, "gate_rejected", {"gate": "g", "node": "verify"}))
    _run(
        lambda c: store.create_session(
            c,
            id="lint",
            work_item_id=wid,
            node_id="verify",
            hook_point="verify.main.lint",
            log_path="/l",
            result_path="/r",
            head_sha="abc",
        )
    )
    _picks(wid, "ws", 0, ["fe-cmd"])

    got = client.get(f"/api/work-items/{wid}").json()["scope_runs"]

    assert [(r["pass"], r["session_id"], r.get("pending")) for r in got] == [
        (1, "old", None),
        (2, None, True),
    ]


def test_detail_names_no_passes_for_a_node_that_ran_once(client, repo, tmp_path):
    wid = _verified_item(client, repo, tmp_path, [("fe-cmd", "done", 0)])

    assert client.get(f"/api/work-items/{wid}").json()["node_passes"] == {}
