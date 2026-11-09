"""`kraft view ...` parser rules and the search verb; the other verbs'
behaviour is in `test_verbs.py` and `test_watching.py`."""

from __future__ import annotations

import json
import re

import pytest

from kraft import cli, client, render
from kraft.cli import view
from kraft.vocab import WorkItemStatus


def test_the_list_filter_offers_the_stored_then_the_display_only_statuses():
    stored = tuple(s.value for s in WorkItemStatus)
    display_only = ("done", "cancelled", "running", "needs_you", "escalated", "failed")
    assert view.STATUSES == (*stored, *display_only)


@pytest.mark.parametrize(
    "word",
    [
        pytest.param("needs-human", id="a-typo"),
        # The board leaves archived items off, so it could only ever match nothing.
        pytest.param("archived", id="archived-is-never-on-the-board"),
    ],
)
def test_list_refuses_a_status_typo_instead_of_showing_an_empty_board(capsys, word):
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "list", "--status", word])
    assert caught.value.code == 2
    assert f"invalid choice: '{word}'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "note", [None, "vector search is failing (403), so this is text search only"]
)
def test_search_prints_why_it_fell_back_to_text(note):
    hit = {"kind": "specs", "repo": "/r", "path": "a.md"}
    payload = {"mode": "fts", "results": [hit], **({"note": note} if note else {})}

    out = view._render_search(payload)

    assert "a.md" in out
    assert (f"note: {note}" in out) if note else ("note:" not in out)


def test_search_renders_results(monkeypatch, capsys):
    """One row per hit, with `--limit` passed through to the API."""
    seen = []

    async def fake_search(q, limit=20):
        seen.append((q, limit))
        hits = [
            {"kind": "spec", "repo": "/r", "path": "specs/a.md", "score": 1.0},
            {"kind": "session", "repo": "/s", "path": "sessions/b.md", "score": 0.5},
        ]
        return {"query": q, "results": hits}

    monkeypatch.setattr("kraft.client.search", fake_search)
    cli.main(["view", "search", "anything", "--limit", "3"])
    rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert rows == [
        ["KIND", "REPO", "PATH"],
        ["spec", "/r", "specs/a.md"],
        ["session", "/s", "sessions/b.md"],
    ]
    cli.main(["view", "search", "anything"])
    assert seen == [("anything", 3), ("anything", 20)]


def test_search_json_is_the_api_payload(app, capsys):
    cli.main(["view", "search", "anything", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "anything"


@pytest.mark.parametrize(
    ("stop", "hinted"),
    [
        ({"kind": "budget", "scope": "work_item"}, True),
        ({"kind": "budget", "limit": {"path": "", "key": "budget_usd", "value": 5}}, True),
        ({"kind": "budget", "scope": "daily"}, False),
        ({"kind": "budget", "scope": "tokens"}, False),
        ({"kind": "gate"}, False),
    ],
    ids=["own-cap", "policy-cap", "daily", "token", "not-budget"],
)
def test_show_names_raise_budget_on_a_stop_it_can_raise(monkeypatch, capsys, stop, hinted):
    """R10a-10: the board offers Raise cap; `kraft view show` printed the stop
    reason and `suggested_action None`, and named no command."""
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "needs_human",
            "current_node_id": "plan",
            "stop_reason": "budget cap reached: $0.04 spent on this work item, cap $0.03.",
            "suggested_action": None,
            "stop": stop,
            "chain_definition": {"nodes": []},
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    assert ("next kraft item raise-budget w1 --usd N" in " ".join(out.split())) is hinted
    assert "chain_definition" not in out
    cli.main(["view", "show", "w1", "--json"])
    assert json.loads(capsys.readouterr().out)["stop"] == stop


def test_show_prints_a_title_1_4_stored_as_plain_text(monkeypatch, capsys):
    """R12F-08: `view list` drops a 1.4-stored title's controls, and `view
    show` printed them to the terminal: an escape and a bidi override."""
    from kraft.client import transport

    title = "first line\nsecond\tline \x1b[31mred\u202eevil"

    async def get(path, **_):
        return {"id": "w1", "status": "paused", "title": title, "chain_definition": {"nodes": []}}

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    assert [c for c in out if c in "\x1b\u202e\t"] == []
    assert "second line [31mredevil" in out
    cli.main(["view", "show", "w1", "--json"])
    assert json.loads(capsys.readouterr().out)["title"] == title


@pytest.mark.parametrize(
    ("extra", "line"),
    [
        ({}, "for retry, since 2026-10-09T09:00:00+00:00"),
        (
            {"storage": {"used_bytes": 12 * 1024**3, "limit_bytes": 10 * 1024**3}},
            "since 2026-10-09T09:00:00+00:00; held by the worktree storage limit: "
            "12G counted against the 10G limit",
        ),
        (
            {"why": "checkout", "held_by": "w2", "held_by_title": "Cache the index"},
            "since 2026-10-09T09:00:00+00:00; "
            "waiting for the checkout: “Cache the index” is using it",
        ),
    ],
    ids=["slot", "storage-limit", "checkout"],
)
def test_show_says_a_queued_item_is_queued_since_when_for_which_verb_and_why(
    monkeypatch, capsys, extra, line
):
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "queued",
            "queued": {"verb": "retry", "since": "2026-10-09T09:00:00+00:00", **extra},
            "chain_definition": {"nodes": []},
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    assert line in capsys.readouterr().out


@pytest.mark.parametrize(
    ("detail", "printed"),
    [
        ({"in_place": True}, ["in_place yes"]),
        ({"in_place": False}, []),
        (
            {"status": "needs_human", "stop": {"kind": "checkout"}, "stop_reason": "in use"},
            ["stop_reason in use", "stop_kind checkout"],
        ),
        (
            {
                "stop": {
                    "kind": "infra",
                    "facts": {"cause": "forge_auth", "sso_url": "https://github.com/sso?x=1"},
                }
            },
            ["sso_url https://github.com/sso?x=1"],
        ),
        ({"stop": {"kind": "infra", "facts": {"sso_url": "https://github.com/sso\x1b[2J"}}}, []),
    ],
    ids=["in-place", "worktree", "checkout-stop", "sso-stop", "unsafe-sso-address-not-printed"],
)
def test_show_marks_an_in_place_item_and_names_the_kind_of_its_stop(
    monkeypatch, capsys, detail, printed
):
    from kraft.client import transport

    async def get(path, **_):
        return {"id": "w1", "status": "paused", "chain_definition": {"nodes": []}, **detail}

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    rows = [" ".join(r.split()) for r in out.splitlines()]
    assert all(line in rows for line in printed)
    assert ("in_place" in out) is bool(detail.get("in_place"))
    assert ("\x1b" in out) is False


def test_show_lists_what_a_blocked_item_comes_after(monkeypatch, capsys):
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "blocked",
            "dependencies": [{"id": "a1", "title": "first", "status": "active", "met": False}],
            "chain_definition": {"nodes": []},
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    assert "a1 (active)" in capsys.readouterr().out


def test_show_prints_what_a_task_flagged_about_its_own_work(monkeypatch, capsys):
    """A task that finished `done_with_concerns` owes the person at the next
    gate its doubt. The detail endpoint carries it; the trimmed view dropped it."""
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "needs_human",
            "pending_gate": "local_review",
            "concerns": ["Failure not reproducible locally; fix is inferred.\x1b[2J"],
            "chain_definition": {"nodes": []},
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    assert "concerns" in out and "Failure not reproducible locally; fix is inferred." in out
    assert "['" not in out  # the text, not a Python list
    assert "\x1b" not in out  # an agent wrote it: no escape reaches the terminal


_HELD = {
    "measured_at": None,
    "state": "held",
    "used_bytes": 12 * 1024**3,
    "quota_bytes": 8 * 1024**3,
    "limit_bytes": 10 * 1024**3,
    "reclaimable_bytes": 0,
    "categories": {"worktrees": 12 * 1024**3},
    "items": [],
    "orphans": [],
}


def test_storage_reads_and_exits_zero_even_when_held(monkeypatch, capsys):
    """A read: it reports a held instance, it does not fail on one."""

    async def storage_usage():
        return _HELD

    monkeypatch.setattr("kraft.client.storage_usage", storage_usage)

    cli.main(["view", "storage"])

    assert "12G of 10G (held)" in capsys.readouterr().out


def test_storage_json_is_the_api_payload(app, capsys):
    cli.main(["view", "storage", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "measured_at",
        "state",
        "used_bytes",
        "quota_bytes",
        "limit_bytes",
        "reclaimable_bytes",
        "categories",
        "items",
        "orphans",
    }
    assert (payload["state"], payload["items"]) == (None, [])


def test_show_and_artifact_name_the_head_the_final_review_takes(monkeypatch, capsys):
    """The final review's approval must carry the head it was shown, so the
    two verbs a person reads it with print it; off that gate there is none."""
    from kraft.client import transport

    shown = {"id": "w1", "status": "needs_human", "review_head": "a" * 40}

    async def get(path, **_):
        return shown

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    assert ["head", "a" * 40] in [line.split() for line in capsys.readouterr().out.splitlines()]
    shown = {"id": "w1", "status": "needs_human", "review_head": None}
    cli.main(["view", "show", "w1"])
    assert "head" not in capsys.readouterr().out.split()

    from kraft import render

    body = render.artifact_body({"content": "doc", "head": "a" * 40, "work_item_id": "w1"})
    assert body.endswith(f"kraft item approve --head {'a' * 40} w1")
    assert "--head" not in render.artifact_body({"content": "doc"})


_MR = {"number": 7, "title": "T", "state": "open", "labels": ["a", "b"], "url": "u"}


@pytest.mark.parametrize(
    ("argv", "fn", "args", "payload", "line"),
    [
        (["mr"], "forge_mr", ("w1", None), _MR, "!7 T [open] a,b u"),
        (["pipelines"], "forge_pipelines", ("w1", None), [], "none"),
        (["jobs", "--pipeline", "5"], "forge_jobs", ("5", "w1", None), [], "none"),
        (
            ["job-log", "--pipeline", "5", "--job", "9", "-n", "50"],
            "forge_job_log",
            ("5", "9", "w1", 50, None),
            {"log": "boom", "truncated": False},
            "boom",
        ),
        (["merged", "--limit", "3"], "forge_merged_mrs", ("w1", 3, None), [], "no merged"),
        (["labels", "--repo", "libs/core"], "forge_labels", ("w1", "libs/core"), [], "no labels"),
        (["mr", "--repo", "libs/core"], "forge_mr", ("w1", "libs/core"), _MR, "!7"),
        (
            ["job-log", "--pipeline", "5", "--job", "9"],
            "forge_job_log",
            ("5", "9", "w1", 200, None),
            {"log": "\x1b]52;c;ZXZpbA==\x07ok", "truncated": False},
            "]52;c;ZXZpbA==ok",
        ),
        (["mr"], "forge_mr", ("w1", None), {**_MR, "title": "T\x9b2J"}, "!7 T2J [open]"),
    ],
    ids=["mr", "pipelines", "jobs", "job-log", "merged", "labels", "repo", "log-osc", "title-c1"],
)
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_the_forge_view_verbs_call_their_client_read(
    monkeypatch, capsys, argv, fn, args, payload, line, as_json
):
    seen = []

    async def read(*a):
        seen.append(a)
        return payload

    monkeypatch.setattr(f"kraft.client.{fn}", read)
    cli.main(["view", argv[0], "w1", *argv[1:], *(["--json"] if as_json else [])])
    assert seen == [args]
    out = capsys.readouterr().out
    assert (json.loads(out) == payload) if as_json else line in out


@pytest.mark.parametrize(
    ("one_off", "line"),
    [
        (
            {"extends": "default", "custom": 0, "needs_review": False, "approval": None},
            "one-off · extends default · library-only",
        ),
        (
            {"extends": "default", "custom": 2, "needs_review": True, "approval": None},
            "one-off · extends default · awaiting review",
        ),
        (
            {
                "extends": "default",
                "custom": 1,
                "needs_review": False,
                "approval": {"by": "human", "door": "web", "at": "2026-10-09T09:00:00+00:00"},
            },
            "one-off · extends default · approved by human (web) 2026-10-09T09:00:00+00:00",
        ),
    ],
    ids=["library-only", "awaiting-review", "approved"],
)
def test_show_names_a_one_off_chain_and_where_it_stands(monkeypatch, capsys, one_off, line):
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "paused",
            "title": "t",
            "chain_definition": {"nodes": []},
            "one_off": one_off,
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    assert line in capsys.readouterr().out


def test_show_keeps_the_raise_hint_after_the_stop_with_a_one_off_chain(monkeypatch, capsys):
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "needs_human",
            "current_node_id": "plan",
            "stop_reason": "budget cap reached",
            "suggested_action": None,
            "stop": {"kind": "budget", "scope": "work_item"},
            "chain_definition": {"nodes": []},
            "one_off": {"extends": "default", "custom": 0, "needs_review": False, "approval": None},
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    keys = [line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert keys.index("stop_reason") < keys.index("stop_kind") < keys.index("next")
    assert keys.index("status") < keys.index("chain")


def test_show_lists_a_parents_slices(monkeypatch, capsys):
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "p1",
            "status": "waiting",
            "title": "the parent",
            "chain_definition": {"nodes": []},
            "slices": {"done": 1, "needs_you": 1, "paused": 0, "running": 0, "queued": 0,
                       "dropped": 0, "total": 2},
            "children": [
                {"id": "c1", "title": "p · slice 1: A", "slice_key": 1, "status": "completed",
                 "current_node_id": "verification", "cost_usd": 0.42},
                {"id": "c2", "title": "p · slice 2: B", "slice_key": 2, "status": "needs_human",
                 "current_node_id": "implementation", "cost_usd": None},
            ],
        }  # fmt: skip

    monkeypatch.setattr(transport, "_get", get)

    cli.main(["view", "show", "p1"])

    out = capsys.readouterr().out
    assert "1 done, 1 needs you of 2" in out
    assert "1  c1  completed  verification  $0.42  p · slice 1: A" in out
    assert "2  c2  needs_human  implementation  -  p · slice 2: B" in out


_ROW = {"id": "w1", "repo": "r", "title": "t", "current_node_id": None, "pending_gate": None}


def _list_board(monkeypatch, capsys, items, *flags) -> str:
    from kraft.client import transport

    monkeypatch.setenv("COLUMNS", "300")

    async def get(path, **_):
        return {"items": items, "cursor": 0}

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "list", "--all", *flags])
    return capsys.readouterr().out


def _board_with_a_parent():
    kids = [
        {
            "id": "c1",
            "title": "p · slice 1: A",
            "slice_key": 1,
            "status": "completed",
            "display_status": "done",
            "current_node_id": "verification",
            "cost_usd": 0.42,
        },
        {
            "id": "c2",
            "title": "p · slice 2: B",
            "slice_key": 2,
            "status": "needs_human",
            "display_status": "needs_you",
            "current_node_id": "implementation",
            "cost_usd": 0.0,
        },
    ]
    slices = {
        "done": 1,
        "needs_you": 1,
        "paused": 0,
        "running": 0,
        "queued": 0,
        "dropped": 0,
        "total": 2,
    }
    row = {"repo": "r", "pending_gate": None, "children": None, "slices": None}
    return [
        {**row, "id": "p1", "status": "waiting", "display_status": "needs_you",
         "current_node_id": "slices", "title": "t", "children": kids, "slices": slices},
        {**row, "id": "w2", "status": "paused", "display_status": "paused",
         "current_node_id": None, "title": "other"},
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("status", "rows", "listed"),
    [
        pytest.param(
            None,
            [(False, "p1", "needs you"), (True, "c1", "done"), (True, "c2", "needs you"),
             (False, "w2", "paused")],
            [("p1", ["c1", "c2"]), ("w2", [])],
            id="every-child-under-its-parent",
        ),
        # A slice is not a board row: a filter finds it through its parent.
        pytest.param(
            "needs_human",
            [(False, "p1", "needs you"), (True, "c2", "needs you")],
            [("p1", ["c2"])],
            id="a-filter-finds-a-child",
        ),
        pytest.param(
            "waiting",
            [(False, "p1", "needs you"), (True, "c1", "done"), (True, "c2", "needs you")],
            [("p1", ["c1", "c2"])],
            id="a-matching-parent-keeps-every-child",
        ),
        pytest.param("paused", [(False, "w2", "paused")], [("w2", [])], id="no-child-matches"),
        # What the STATUS column prints, pasted back: a display-only word, with its space.
        pytest.param(
            "needs you",
            [(False, "p1", "needs you"), (True, "c1", "done"), (True, "c2", "needs you")],
            [("p1", ["c1", "c2"])],
            id="a-display-word-as-the-column-prints-it",
        ),
    ],
)  # fmt: skip
def test_list_shows_a_parents_children_under_it(monkeypatch, capsys, status, rows, listed):
    flag = ["--status", status] if status else []
    # `_list_board`'s wide terminal keeps the slice row's last column from an ellipsis
    out = render.strip_ansi(_list_board(monkeypatch, capsys, _board_with_a_parent(), *flag))
    payload = json.loads(_list_board(monkeypatch, capsys, _board_with_a_parent(), "--json", *flag))

    lines = out.splitlines()[1:]
    shown = []
    for line in lines:
        ident, word = re.split(r" {2,}", line.strip())[:2]
        shown.append((line.startswith("  "), ident, word.split(" (slices:")[0]))
    assert shown == rows
    if status is None:
        assert "slices: 1 done, 1 needs you of 2" in lines[0]
        assert lines[1].split()[2:] == ["-", "verification", "$0.42", "p", "·", "slice", "1:", "A"]
    assert [(i["id"], [k["id"] for k in i.get("children", [])]) for i in payload] == listed


@pytest.mark.parametrize(
    ("status", "display", "printed"),
    [
        pytest.param("active", "running", "running", id="running"),
        pytest.param("needs_human", "failed", "failed", id="a-stop-is-failed"),
        pytest.param("needs_human", "needs_you", "needs you", id="needs-you"),
        # An older server sends no display status: its stored word, not a guess.
        pytest.param("active", None, "active", id="no-display-status"),
        # A newer server's word this client has no colour for is printed, not a crash.
        pytest.param("active", "someday", "someday", id="unknown-display-status"),
    ],
)
def test_list_prints_the_display_status(monkeypatch, capsys, status, display, printed):
    row = {**_ROW, "status": status}
    if display is not None:
        row["display_status"] = display
    line = _list_board(monkeypatch, capsys, [row]).splitlines()[1]
    assert re.split(r" {2,}", line.strip())[1] == printed


def test_list_paints_running_blue_and_an_old_servers_row_not_at_all(monkeypatch, capsys):
    monkeypatch.setattr(render, "use_color", lambda stream=None: True)
    rows = [
        {**_ROW, "status": "active", "display_status": "running"},
        {**_ROW, "id": "w2", "status": "active"},
    ]
    out = _list_board(monkeypatch, capsys, rows)
    assert "\033[34mrunning\033[0m" in out
    assert "\033[32m" not in out


@pytest.mark.parametrize(
    ("row", "line"),
    [
        pytest.param(
            {"status": "needs_human", "display_status": "needs_you"},
            "display_status  needs you",
            id="needs-you-with-a-space",
        ),
        # An older server sends none: no line, and no KeyError.
        pytest.param({"status": "active"}, None, id="an-older-server-sends-none"),
    ],
)
def test_show_prints_the_display_status_as_the_list_does(row, line):
    item = {**row, "id": "w1", "current_node_id": None, "chain_definition": {"nodes": []}}
    out = render.strip_ansi(view._render_show(client.trim_work_item(item)))
    shown = [re.sub(r" {2,}", "  ", text) for text in out.splitlines()]
    assert [t for t in shown if t.startswith("display_status")] == ([line] if line else [])
