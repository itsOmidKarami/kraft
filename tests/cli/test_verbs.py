"""Every verb through cli.main(), with client.transport.http() on the ASGI app.

This tests the dispatch table and the rendering, not httpx: the client layer has
its own tests in test_client_*.py.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import subprocess

import pytest
from support.harness import make_repo

from kraft import cli, client


def test_mcp_still_dispatches(monkeypatch):
    called = []
    import kraft.mcp as mcp

    monkeypatch.setattr(mcp, "serve_stdio", lambda: called.append(True))
    cli.main(["admin", "mcp"])
    assert called == [True]


def test_init_still_dispatches_and_honours_repo_scope(monkeypatch, tmp_path, capsys):
    seen = {}
    import kraft.init as init_mod

    def fake_install(repo_scope):
        seen["repo_scope"] = repo_scope
        return [tmp_path / "written.json"]

    monkeypatch.setattr(init_mod, "install", fake_install)
    cli.main(["admin", "init", "--repo"])
    assert seen["repo_scope"] is True
    assert "written.json" in capsys.readouterr().out


def _connect(repo):
    import asyncio

    return asyncio.run(client.ensure_repo(str(repo)))


def test_list_renders_a_table_with_a_header(app, capsys, make_item, repo):
    make_item(repo, "first thing")
    cli.main(["view", "list"])
    out = capsys.readouterr().out
    assert "ID" in out.splitlines()[0] and "TITLE" in out.splitlines()[0]
    assert "first thing" in out


def test_list_json_matches_the_client_payload(app, capsys, make_item, repo):
    import asyncio

    make_item(repo, "first thing")
    cli.main(["view", "list", "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed == asyncio.run(client.list_work_items())


def test_list_filters_by_status(app, capsys, make_item, repo):
    make_item(repo, "first thing")
    cli.main(["view", "list", "--status", "completed", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_list_scopes_to_the_cwd_repo(app, tmp_path, monkeypatch, capsys, make_item):
    here = make_repo(tmp_path, name="here")
    elsewhere = make_repo(tmp_path, name="elsewhere")
    _connect(here)
    make_item(here, "mine")
    make_item(elsewhere, "theirs")
    monkeypatch.chdir(here)
    cli.main(["view", "list", "--json"])
    titles = [item["title"] for item in json.loads(capsys.readouterr().out)]
    assert titles == ["mine"]


def test_list_all_ignores_the_cwd_scope(app, tmp_path, monkeypatch, capsys, make_item):
    here = make_repo(tmp_path, name="here")
    elsewhere = make_repo(tmp_path, name="elsewhere")
    _connect(here)
    make_item(here, "mine")
    make_item(elsewhere, "theirs")
    monkeypatch.chdir(here)
    cli.main(["view", "list", "--all", "--json"])
    titles = sorted(item["title"] for item in json.loads(capsys.readouterr().out))
    assert titles == ["mine", "theirs"]


def test_show_takes_an_explicit_id(app, capsys, make_item, repo):
    wid = make_item(repo, "detail me")
    cli.main(["view", "show", wid])
    out = capsys.readouterr().out
    assert wid in out and "detail me" in out


def test_show_defaults_to_the_work_item_this_session_is_in(
    app, monkeypatch, capsys, make_item, repo
):
    wid = make_item(repo, "implicit")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["view", "show"])
    assert "implicit" in capsys.readouterr().out


def test_show_with_no_context_names_both_ways_to_fix_it(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "show"])
    assert caught.value.code == 1
    assert "no work item" in capsys.readouterr().err


def test_search_renders_results(app, capsys):
    cli.main(["view", "search", "anything", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "anything"


def test_an_operation_failure_is_a_kraft_message_on_stderr(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "show", "no-such-item"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean so --json stays pipeable
    assert captured.err.startswith("kraft: ")
    assert "404" in captured.err


def test_create_uses_the_cwd_repo_and_lands_paused(app, monkeypatch, capsys, repo):
    _connect(repo)
    monkeypatch.chdir(repo)
    cli.main(["item", "create", "filed from a terminal", "--json"])
    created = json.loads(capsys.readouterr().out)
    assert created["title"] == "filed from a terminal"
    # an agent (or a human) files work; a human starts it from the board
    assert created["status"] == "paused"


def test_create_carries_the_description(app, monkeypatch, capsys, repo):
    _connect(repo)
    monkeypatch.chdir(repo)
    cli.main(["item", "create", "short label", "--description", "the brief", "--json"])
    created = json.loads(capsys.readouterr().out)

    cli.main(["view", "show", created["id"], "--json"])
    assert json.loads(capsys.readouterr().out)["description"] == "the brief"


def test_item_create_passes_auto_gate(monkeypatch):
    seen = {}

    async def fake_create(title, repo, chain, description, attachments, auto_gate=False, **_rest):
        seen["auto_gate"] = auto_gate
        return {"id": "w1"}

    monkeypatch.setattr("kraft.client.create_work_item", fake_create)
    cli.main(["item", "create", "t", "--repo", "/r", "--auto-gate"])
    assert seen["auto_gate"] is True


def test_an_items_own_policy_is_set_at_create_and_replaced_by_set_policy(
    app, monkeypatch, capsys, repo
):
    """Kraft-ab1bh: `--policy FIELD=VALUE` item-wide, `PATH.FIELD=VALUE` for
    one scope, a value read as YAML; `set-policy` replaces the whole override
    and `--clear` drops it."""
    _connect(repo)
    monkeypatch.chdir(repo)
    ci = "merge_request_feedback.ci.await_ci"
    cli.main(["item", "create", "t", "--policy", "max_attempts=3",
              "--policy", f"{ci}.wait_timeout_minutes=180",
              "--policy", "deny_tools=[WebFetch]", "--json"])  # fmt: skip
    wid = json.loads(capsys.readouterr().out)["id"]

    def stored():
        cli.main(["view", "show", wid, "--json"])
        return json.loads(capsys.readouterr().out).get("policy_override")

    assert stored() == {
        "max_attempts": 3,
        "deny_tools": ["WebFetch"],
        "paths": {ci: {"wait_timeout_minutes": 180}},
    }
    cli.main(["item", "set-policy", wid, "--policy", "verification.max_attempts=2", "--json"])
    capsys.readouterr()
    assert stored() == {"paths": {"verification": {"max_attempts": 2}}}
    cli.main(["item", "set-policy", wid, "--clear", "--json"])
    capsys.readouterr()
    assert stored() is None


def test_create_outside_a_connected_repo_says_how_to_fix_it(app, tmp_path, monkeypatch, capsys):
    stranger = make_repo(tmp_path, name="stranger")
    monkeypatch.chdir(stranger)
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "create", "nowhere"])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    # the advice matches the situation: this is a git repo, it just is not
    # connected, so `kraft connect` is the fix and the message must name it
    assert "kraft repo connect" in err
    assert str(stranger) in err


def test_create_outside_any_git_repo_says_something_else(app, tmp_path, monkeypatch, capsys):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    monkeypatch.chdir(plain)
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "create", "nowhere"])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "kraft connect" not in err  # there is nothing here to connect
    assert "no repo" in err


def test_create_through_the_mcp_door_resolves_the_cwd_repo(app, monkeypatch, repo):
    """The resolution the CLI does in `_repo_scope` lives in `client.py` too, so
    an agent standing in a connected repo does not have to name it (spec F §2.4)."""
    import asyncio

    _connect(repo)
    monkeypatch.chdir(repo)
    created = asyncio.run(client.create_work_item("filed by an agent"))
    assert created["status"] == "paused"


def test_create_attaches_a_spec_from_the_flag(app, tmp_path, monkeypatch, capsys, repo):
    """Kraft-82gz end to end: a spec that exists only in the worktree the agent
    is standing in, named by a relative path, reaching the stored item.

    `--repo` is explicit because `client.resolve_repo` walks the *parents* of a
    linked worktree and finds no connected repo there (Kraft-tc33); a Kraft
    worker does not hit that, it inherits its repo from $KRAFT_WORK_ITEM_ID.
    """
    _connect(repo)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# settled in this session\n")
    monkeypatch.chdir(worktree)

    cli.main(
        [
            "item",
            "create",
            "handed over",
            "--repo",
            str(repo),
            "--spec",
            ".engineering/specs/s.md",
            "--json",
        ]
    )
    created = json.loads(capsys.readouterr().out)

    cli.main(["view", "show", created["id"], "--json"])
    item = json.loads(capsys.readouterr().out)
    assert [a["kind"] for a in item["attachments"]] == ["spec"]
    assert item["attachments"][0]["path"] == ".engineering/specs/s.md"

    # the attached spec trims the gate it satisfies — `view show` trims the
    # chain itself away, so check the untrimmed item straight from the API
    import asyncio

    async def _fetch_full():
        async with client.transport.http() as http:
            return (await http.get(f"/api/work-items/{created['id']}")).json()

    full = asyncio.run(_fetch_full())
    assert "spec_approval" not in [n["gate_after"] for n in full["chain_definition"]["nodes"]]


def test_create_through_the_mcp_door_attaches(app, tmp_path, monkeypatch, repo):
    """The MCP tool's whole body is this client call (mcp.py is a dispatch table
    and nothing more), so the round-trip is tested here and the tool's own
    schema is tested in test_mcp.py."""
    import asyncio

    _connect(repo)
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# the plan\n")
    monkeypatch.chdir(repo)

    created = asyncio.run(
        client.create_work_item(
            "filed by an agent",
            attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
        )
    )
    item = asyncio.run(client.get_work_item(created["id"]))
    # Intake copies the plan into Kraft's own storage and reports that copy as
    # `source`, even though the original was found under the repo root
    # (Kraft-eqgn): the trim it justifies has to outlive the caller's worktree.
    assert [{k: v for k, v in a.items() if k != "source"} for a in item["attachments"]] == [
        {"kind": "plan", "path": ".engineering/plans/p.md"}
    ]
    stored_dir = tmp_path / "run" / "attachments" / created["id"]
    assert pathlib.Path(item["attachments"][0]["source"]).is_relative_to(stored_dir)


def test_reject_requires_a_note(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "reject", "Kraft-whatever"])
    assert caught.value.code == 2  # missing required argument is a usage error
    assert "--note" in capsys.readouterr().err


def test_a_worker_cannot_act_on_its_own_work_item(app, monkeypatch, capsys, make_item, repo):
    """The §6 rule-2 guard fires through the CLI door too, not only through MCP."""
    wid = make_item(repo, "mine to do, not to approve")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "approve"])
    assert caught.value.code == 1
    assert "cannot act on its own work item" in capsys.readouterr().err


def test_a_worker_cannot_set_its_own_policy_through_the_cli(
    app, monkeypatch, capsys, make_item, repo
):
    """Kraft-j89jc: `kraft item set-policy` from inside the item's own worker."""
    wid = make_item(repo, "mine to run, not to loosen")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "set-policy", "--policy", "max_attempts=9"])
    assert caught.value.code == 1
    assert "cannot act on its own work item" in capsys.readouterr().err
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID")
    cli.main(["view", "show", wid, "--json"])
    assert "policy_override" not in json.loads(capsys.readouterr().out)


def test_resume_starts_a_paused_item(app, capsys, make_item, repo):
    wid = make_item(repo, "start me")
    cli.main(["item", "resume", wid, "--steer", "go left", "--json"])
    assert capsys.readouterr().out.strip()  # the API's response, whatever shape it has


def test_pause_on_a_paused_item_surfaces_the_api_error(app, capsys, make_item, repo):
    wid = make_item(repo, "already paused")
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "pause", wid])
    assert caught.value.code == 1
    assert capsys.readouterr().err.startswith("kraft: ")


@pytest.mark.parametrize(
    ("argv", "fn", "expected"),
    [
        (
            ["item", "retry", "w1", "--steer", "try the other adapter"],
            "retry",
            {
                "steer": "try the other adapter",
                "work_item_id": "w1",
                "path": None,
                "restart": False,
            },
        ),
        (
            ["item", "retry", "w1", "--path", "verification.review.code_review"],
            "retry",
            {
                "steer": None,
                "work_item_id": "w1",
                "path": "verification.review.code_review",
                "restart": False,
            },
        ),
        (
            ["item", "retry", "w1", "--restart"],
            "retry",
            {"steer": None, "work_item_id": "w1", "path": None, "restart": True},
        ),
        (
            ["item", "skip", "w1", "--note", "known flake"],
            "skip",
            {"note": "known flake", "work_item_id": "w1", "path": None},
        ),
        (
            ["item", "skip", "w1", "--path", "verification.review"],
            "skip",
            {"note": None, "work_item_id": "w1", "path": "verification.review"},
        ),
        (
            ["item", "resume", "w1", "--steer", "all", "--steer-task", "a.main.b=only b"],
            "resume",
            {"steer": "all", "work_item_id": "w1", "steers": {"a.main.b": "only b"}},
        ),
        (
            ["item", "complete", "w1", "--reason", "shipped by hand"],
            "complete",
            {"reason": "shipped by hand", "work_item_id": "w1", "close_beads": False},
        ),
        (
            ["item", "complete", "w1", "--reason", "shipped by hand", "--close-beads"],
            "complete",
            {"reason": "shipped by hand", "work_item_id": "w1", "close_beads": True},
        ),
        (
            ["item", "cancel", "w1", "--reason", "not needed"],
            "cancel",
            {"reason": "not needed", "work_item_id": "w1"},
        ),
        (
            ["item", "escalate", "w1", "--message", "please look at this"],
            "escalate",
            {"message": "please look at this", "work_item_id": "w1", "new_thread": False},
        ),
        (
            ["item", "escalate", "w1", "--message", "please look at this", "--new-thread"],
            "escalate",
            {"message": "please look at this", "work_item_id": "w1", "new_thread": True},
        ),
        (["item", "progress", "2", "w1"], "report_progress", {"task": 2, "work_item_id": "w1"}),
    ],
    ids=[
        "retry-steer",
        "retry-path",
        "retry-restart",
        "skip-note",
        "skip-path",
        "resume-steer-task",
        "complete-reason",
        "complete-close-beads",
        "cancel-reason",
        "escalate-message",
        "escalate-new-thread",
        "progress-task",
    ],
)
def test_a_verb_passes_its_arguments_through(app, monkeypatch, capsys, argv, fn, expected):
    """Each argument lands on the client function's own parameter, as the real
    function would bind it (defaults included), and the reply names the item."""
    real = getattr(client, fn)
    seen = {}

    async def fake(*args, **kwargs):
        bound = inspect.signature(real).bind(*args, **kwargs)
        bound.apply_defaults()
        seen.update(bound.arguments)
        return {
            "id": "w1",
            "node_id": "n",
            "steer": None,
            "status": "active",
            "progress": {"current": 2, "total": 3, "title": "serve"},
        }

    monkeypatch.setattr(client, fn, fake)
    cli.main(argv)
    assert seen == expected
    assert "w1" in capsys.readouterr().out


def test_reject_passes_the_node_through(app, monkeypatch, capsys):
    seen = {}

    async def fake_reject(note, gate=None, work_item_id=None, node=None):
        seen.update(note=note, gate=gate, work_item_id=work_item_id, node=node)
        return {"id": work_item_id, "status": "active"}

    monkeypatch.setattr(client, "reject_gate", fake_reject)
    cli.main(
        [
            "item",
            "reject",
            "w1",
            "--note",
            "the retry path is untested",
            "--gate",
            "human_review_approval",
            "--node",
            "implementation",
        ]
    )
    assert seen == {
        "note": "the retry path is untested",
        "gate": "human_review_approval",
        "work_item_id": "w1",
        "node": "implementation",
    }


GROUPS = {
    "item": [
        "create",
        "approve",
        "reject",
        "pause",
        "resume",
        "retry",
        "abandon",
        "complete",
        "cancel",
    ],
    "view": [
        "list",
        "show",
        "search",
        "logs",
        "events",
        "watch",
        "diff",
        "docs",
        "doc",
        "artifact",
    ],
    "repo": ["list", "connect", "disconnect", "path", "cd", "open"],
    "admin": ["start", "stop", "health", "doctor", "reindex", "init", "mcp", "update"],
}


@pytest.mark.parametrize("group,verb", [(g, v) for g, verbs in GROUPS.items() for v in verbs])
def test_every_grouped_verb_parses_to_a_handler(group, verb):
    """The tree is the interface. Each leaf must reach a callable."""
    args = [group, verb]
    if verb == "search":
        args.append("query")
    if verb == "doc":
        args.append("doc-1")
    if verb == "create":
        args.append("a title")
    if verb == "reject":
        args += ["--note", "why"]
    if verb in ("complete", "cancel"):
        args += ["--reason", "why"]
    ns = cli.build_parser().parse_args(args)
    assert callable(ns.func)


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_a_group_with_no_verb_is_a_usage_error(group, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main([group])
    assert caught.value.code == 2


def test_every_moved_verb_names_its_new_path(capsys):
    """A removed verb must say where it went. argparse's own error does not."""
    for old, new in cli.MOVED.items():
        with pytest.raises(SystemExit) as caught:
            cli.main([old])
        assert caught.value.code == 2
        err = capsys.readouterr().err
        assert old in err and f"kraft {new}" in err


def test_moved_covers_every_verb_that_existed():
    """A verb dropped from MOVED is a verb that vanishes silently."""
    expected = {
        "create",
        "approve",
        "reject",
        "pause",
        "resume",
        "retry",
        "abandon",
        "list",
        "show",
        "search",
        "logs",
        "events",
        "watch",
        "diff",
        "docs",
        "doc",
        "artifact",
        "repos",
        "connect",
        "disconnect",
        "path",
        "cd",
        "open",
        "health",
        "doctor",
        "reindex",
        "init",
        "mcp",
        "serve",
    }
    assert set(cli.MOVED) == expected


def test_no_source_string_tells_a_user_to_run_a_removed_verb():
    """A hint that names a dead command is worse than no hint at all."""
    cli_dir = pathlib.Path(cli.__file__).parent
    src = cli_dir.parent
    pattern = re.compile(r"`kraft (" + "|".join(sorted(cli.MOVED)) + r")\b")
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if path.parent == cli_dir:  # the cli package itself defines every one of them
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(src)}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_show_renders_progress_as_a_task_list(app, monkeypatch, capsys):
    async def fake_get(work_item_id=None):
        return {
            "id": "w1",
            "current_node_id": "implementation",
            "progress": {
                "current": 2,
                "total": 3,
                "title": "serve",
                "tasks": [
                    {"n": 1, "title": "parse", "state": "done"},
                    {"n": 2, "title": "serve", "state": "current"},
                    {"n": 3, "title": "render", "state": "pending"},
                ],
            },
        }

    monkeypatch.setattr(client, "get_work_item", fake_get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    assert "2 of 3 · serve" in out
    # A bare "task" could mean a chain task; the plan task's title says which.
    assert "task" not in out.lower()
    assert "✓ 1. parse" in out
    assert "▸ 2. serve" in out
    assert "· 3. render" in out


def test_list_puts_progress_after_the_node(app, monkeypatch, capsys):
    async def fake_list(status=None, include_abandoned=False):
        return [
            {
                "id": "w1",
                "title": "t",
                "repo": "/r",
                "status": "active",
                "current_node_id": "implementation",
                "pending_gate": None,
                "progress": {"current": 2, "total": 3, "title": "serve"},
            }
        ]

    monkeypatch.setattr(client, "list_work_items", fake_list)
    cli.main(["view", "list", "--all"])
    assert "implementation 2/3" in capsys.readouterr().out
