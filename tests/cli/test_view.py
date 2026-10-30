"""`kraft view ...` parser rules and the search verb; the other verbs'
behaviour is in `test_verbs.py` and `test_watching.py`."""

from __future__ import annotations

import json

import pytest

from kraft import cli
from kraft.cli import view
from kraft.vocab import WorkItemStatus


def test_the_list_filter_offers_exactly_the_work_item_statuses():
    assert view.STATUSES == tuple(s.value for s in WorkItemStatus)


def test_list_refuses_a_status_typo_instead_of_showing_an_empty_board(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "list", "--status", "needs-human"])
    assert caught.value.code == 2
    assert "invalid choice: 'needs-human'" in capsys.readouterr().err


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
    ],
    ids=["in-place", "worktree", "checkout-stop"],
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
