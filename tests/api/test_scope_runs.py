"""`scope_runs` on the item detail: every command a changed-test-scope task ran, over every round
and repository, and the ones its rounds picked and have not started."""

from __future__ import annotations

from kraft import events

from .test_board_stop import _paused_item, _run, _verified_item


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


def test_detail_scope_runs_lists_what_a_round_picked_and_has_not_started(client, repo, tmp_path):
    """`test_scopes_selected` names the commands a round will run in a repository before it runs
    any: the ones with no session yet come last as `pending`, with no session, and every entry of
    a round that recorded its picks is `selected`, so what it dropped is known before it ends."""
    wid = _verified_item(
        client, repo, tmp_path, [("fe-cmd", "done", 0, 1, "ws"), ("be-cmd", "running", 0, 1, "ws")]
    )
    for repository, rnd, commands in (
        ("ws", 1, ["fe-cmd", "be-cmd", "docs-cmd"]),
        ("pkg", 1, ["be-cmd"]),
    ):
        _run(
            lambda c, r=repository, n=rnd, cmds=commands: events.append(
                c,
                wid,
                "test_scopes_selected",
                {
                    "node_id": "verify",
                    "hook_point": "verify.main.t",
                    "repository": r,
                    "round": n,
                    "commands": cmds,
                },
            )
        )

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
    assert {r["session_id"] for r in got} - {None} == {"s0", "s1"}
