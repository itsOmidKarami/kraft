"""`kraft item`'s own parser: what `kraft item --help` and `kraft item create
--help` say, where a reviewer could not tell what a verb or argument was, what
`create`'s flags default to, and what `kraft item create` prints."""

from __future__ import annotations

import asyncio
import json

import pytest
from support.api import _set_status

from kraft import cli


def _help(capsys, *argv: str) -> str:
    with pytest.raises(SystemExit):
        cli.main([*argv, "--help"])
    return " ".join(capsys.readouterr().out.split())


def test_raise_budget_help_says_which_cap_it_raises(capsys):
    """The item's own cap or its policy's item-wide one: a node's, a token or
    the daily cap's stop is refused."""
    text = _help(capsys, "item")
    assert (
        "raise-budget raise the dollar cap that stopped an item, its own (create --budget) "
        "or its policy's item-wide budget_usd, and retry it"
    ) in text


def test_set_overrides_help_says_each_call_replaces_the_override(capsys):
    """`set-node-override` merges and this one does not, and a person who ran
    one then the other lost the model without being told."""
    text = _help(capsys, "item", "set-overrides")
    assert "Each call replaces the item-wide override" in text
    assert "a flag you leave out goes back to the template's own binding" in text


def test_set_node_override_help_says_it_merges(capsys):
    text = _help(capsys, "item", "set-node-override")
    assert "Each call changes only the flags you give" in text and "--clear resets the node" in text


def test_create_help_says_an_attachment_is_a_file_inside_the_repo(capsys):
    """R10a-02: a spec kept in ~/notes was refused, and nothing said where it
    had to be."""
    text = _help(capsys, "item", "create")
    assert "--spec PATH attach a spec that already exists, a file inside the repo" in text
    assert "--plan PATH attach a plan that already exists, a file inside the repo" in text


def test_create_gives_its_title_a_help_line(capsys):
    assert "title the item's title: one line, as the board shows it" in _help(
        capsys, "item", "create"
    )


@pytest.mark.parametrize(
    ("status", "advice"),
    [
        ("active", "To keep them, cancel the item instead."),
        (
            "abandoned",
            "This item is already cancelled; abandoning it only reclaims its worktree and branch.",
        ),
    ],
    ids=["open", "cancelled"],
)
def test_abandon_refusal_fits_the_items_state(monkeypatch, capsys, status, advice):
    """It used to tell a cancelled item's owner to cancel it."""

    async def get_work_item(item_id=None, **_):
        return {"id": item_id, "status": status}

    monkeypatch.setattr(cli.item.client, "get_work_item", get_work_item)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "abandon", "w1"])
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert advice in err and "pass --yes" in err
    assert ("cancel the item instead" in err) is (status != "abandoned")


def test_abandon_refusal_still_works_when_the_item_cannot_be_read(monkeypatch, capsys):
    async def get_work_item(item_id=None, **_):
        raise ConnectionError("no server")

    monkeypatch.setattr(cli.item.client, "get_work_item", get_work_item)
    with pytest.raises(SystemExit):
        cli.main(["item", "abandon", "w1"])
    assert "To keep them, cancel the item instead." in capsys.readouterr().err


_QUEUED_ANSWER = {"id": "w1", "status": "queued", "slots": {"busy": 3, "limit": 3}}
_QUEUED_LINE = "queued w1: 3 of 3 slots are busy. It starts when one frees"


def test_create_autostart_at_the_slot_limit_says_it_is_queued(tmp_path, monkeypatch, capsys):
    """The board's composer says so; the CLI must not print `status queued`
    with no reason."""
    from kraft.client import transport

    async def post(path, payload=None, **kw):
        assert payload["autostart"] is True
        return 201, _QUEUED_ANSWER

    monkeypatch.setattr(transport, "_post", post)
    cli.main(["item", "create", "t", "--repo", str(tmp_path), "--autostart"])
    assert _QUEUED_LINE in capsys.readouterr().out
    cli.main(["item", "create", "t", "--repo", str(tmp_path), "--autostart", "--json"])
    assert json.loads(capsys.readouterr().out)["slots"] == {"busy": 3, "limit": 3}


@pytest.mark.parametrize(
    "argv",
    [
        ["item", "resume", "w1"],
        ["item", "retry", "w1"],
        ["item", "raise-budget", "w1", "--usd", "5"],
    ],
    ids=["resume", "retry", "raise-budget"],
)
def test_a_start_on_a_full_board_says_it_is_queued(monkeypatch, capsys, argv):
    """A queued answer is not an item row and not the door's usual answer:
    printed as either, it read `started w1` for an item that had not."""
    from kraft.client import transport

    async def post(path, payload=None, **kw):
        return 200, _QUEUED_ANSWER

    monkeypatch.setattr(transport, "_post", post)
    cli.main(argv)
    out = capsys.readouterr().out
    assert _QUEUED_LINE in out
    assert "started" not in out and "retried at" not in out


def test_create_after_sends_what_the_item_comes_after(tmp_path, monkeypatch):
    from kraft.client import transport

    sent = {}

    async def post(path, payload=None, **kw):
        sent.update(payload)
        return 201, {"id": "w1", "status": "paused"}

    monkeypatch.setattr(transport, "_post", post)
    cli.main(["item", "create", "t", "--repo", str(tmp_path), "--after", "a1", "--after", "b2"])
    assert sent["depends_on"] == ["a1", "b2"]


def test_unblock_names_the_dependency_it_drops(monkeypatch, capsys):
    from kraft.client import transport

    seen = {}

    async def post(path, payload=None, **kw):
        seen.update(path=path, payload=payload)
        return 200, {"id": "w1", "dropped": ["a1"], "waiting_on": []}

    monkeypatch.setattr(transport, "_post", post)
    cli.main(["item", "unblock", "w1", "--dependency", "a1"])
    assert seen["path"].endswith("/work-items/w1/unblock")
    assert seen["payload"] == {"dependency": "a1"}
    assert "w1 no longer comes after a1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["item", "resume", "w1"],
        ["item", "retry", "w1"],
        ["item", "raise-budget", "w1", "--usd", "5"],
    ],
    ids=["resume", "retry", "raise-budget"],
)
def test_a_start_behind_an_unfinished_item_says_it_is_blocked(monkeypatch, capsys, argv):
    from kraft.client import transport

    async def post(path, payload=None, **kw):
        return 200, {
            "id": "w1",
            "status": "blocked",
            "waiting_on": [{"id": "a1", "title": "first", "status": "active"}],
        }

    monkeypatch.setattr(transport, "_post", post)
    cli.main(argv)
    out = capsys.readouterr().out
    assert "blocked w1: it comes after a1 (active). It starts when they complete" in out
    assert "started" not in out and "retried at" not in out


@pytest.mark.parametrize(
    ("flag", "auto_gate"),
    [([], True), (["--auto-gate"], True), (["--no-auto-gate"], False)],
    ids=["on-by-default", "on", "off"],
)
def test_item_create_passes_auto_gate(monkeypatch, flag, auto_gate):
    seen = {}

    async def fake_create(title, repo, chain, description, attachments, *, auto_gate, **_rest):
        seen["auto_gate"] = auto_gate
        return {"id": "w1"}

    monkeypatch.setattr("kraft.client.create_work_item", fake_create)
    cli.main(["item", "create", "t", "--repo", "/r", *flag])
    assert seen["auto_gate"] is auto_gate


#: A whole row, as the gate and lifecycle routes hand one back: its frozen
#: chain alone ran a real `kraft item approve` to 30 KB (R7a-05).
_ROW = {
    "id": "w1",
    "status": "active",
    "current_node_id": "spec_approval",
    "chain_definition": "{}",
    "materialized_chain": "x" * 30_000,
}


def test_approve_prints_one_line_naming_the_pending_gate(monkeypatch, capsys):
    """No `--gate`: the line names the gate that was pending, and `--json`
    still prints the whole row."""
    from kraft.client import transport

    async def get(path, **_):
        return {"id": "w1", "pending_gate": "spec_approval"}

    async def act(path, payload=None):
        assert path == "/work-items/w1/gates/spec_approval/approve"
        return dict(_ROW)

    monkeypatch.setattr(transport, "_get", get)
    monkeypatch.setattr(transport, "_act", act)
    cli.main(["item", "approve", "w1"])
    assert capsys.readouterr().out == "approved spec_approval on w1; the item is now running\n"
    cli.main(["item", "approve", "w1", "--json"])
    assert json.loads(capsys.readouterr().out) == _ROW


@pytest.mark.parametrize(
    ("argv", "fn", "said"),
    [
        (
            ["item", "reject", "w1", "--gate", "plan_approval", "--note", "no"],
            "reject_gate",
            "rejected plan_approval on w1; the item is now running",
        ),
        (["item", "resume", "w1"], "resume", "resumed w1; the item is now running"),
        (["item", "retry", "w1"], "retry", "retried w1; the item is now running"),
        (
            ["item", "raise-budget", "w1", "--usd", "2.5"],
            "raise_budget",
            "raised the cap on w1 to $2.5 and retried it; the item is now running",
        ),
        (
            ["item", "skip", "w1"],
            "skip",
            "skipped the current node on w1; the item is now running",
        ),
        (
            ["item", "skip", "w1", "--path", "verify.main"],
            "skip",
            "skipped verify.main on w1; the item is now running",
        ),
        (["item", "complete", "w1", "--reason", "done"], "complete", "marked w1 complete"),
        (
            ["item", "cancel", "w1", "--reason", "dropped"],
            "cancel",
            "cancelled w1; its worktree and branch stay (kraft item abandon w1 --yes deletes them)",
        ),
        (
            ["item", "review", "w1", "approve"],
            "submit_review",
            "sent your review (approve) on w1; the item is now running",
        ),
    ],
    ids=[
        "reject",
        "resume",
        "retry",
        "raise-budget",
        "skip",
        "skip-path",
        "complete",
        "cancel",
        "review",
    ],
)
def test_a_verb_answered_with_the_whole_row_prints_one_line(monkeypatch, capsys, argv, fn, said):
    async def fake(*_a, **_kw):
        return dict(_ROW)

    monkeypatch.setattr(cli.item.client, fn, fake)
    cli.main(argv)
    assert capsys.readouterr().out == said + "\n"


def test_a_stopped_item_says_where_to_read_why(monkeypatch, capsys):
    async def fake(*_a, **_kw):
        return {**_ROW, "status": "needs_human"}

    monkeypatch.setattr(cli.item.client, "retry", fake)
    cli.main(["item", "retry", "w1"])
    out = capsys.readouterr().out
    assert out == "retried w1; the item is now stopped for a person: kraft view show w1 says why\n"


@pytest.mark.parametrize(
    ("argv", "fn", "answer", "said"),
    [
        (
            ["item", "resume", "w1"],
            "resume",
            {"id": "w1", "node_id": "plan", "steer": "go", "steered": ["plan.main.author"]},
            "resumed w1 at plan; steered plan.main.author",
        ),
        (
            ["item", "resume", "w1"],
            "resume",
            {"id": "w1", "node_id": None, "steer": None, "steered": []},
            "started w1",
        ),
        (
            ["item", "retry", "w1"],
            "retry",
            {"id": "w1", "node_id": "verify", "path": None, "loop": None, "attempt": 3},
            "retried w1 at verify, attempt 3",
        ),
        (
            ["item", "retry", "w1", "--path", "verify.main"],
            "retry",
            {"id": "w1", "node_id": "verify", "path": "verify.main", "attempt": 2},
            "retried w1 at verify.main, attempt 2",
        ),
        (
            ["item", "raise-budget", "w1", "--usd", "25"],
            "raise_budget",
            {"id": "w1", "node_id": "plan", "path": None, "attempt": 2},
            "raised the cap on w1 to $25; retried at plan, attempt 2",
        ),
        (
            ["item", "raise-budget", "w1", "--usd", "none"],
            "raise_budget",
            {"id": "w1", "node_id": "plan", "path": None, "attempt": None},
            "raised the cap on w1 to no cap; retried at plan",
        ),
        (
            ["item", "pause", "w1"],
            "pause",
            {"id": "w1", "paused_sessions": ["s1", "s2"]},
            "paused w1, stopping 2 running sessions; kraft item resume w1 carries on",
        ),
        (
            ["item", "pause", "w1"],
            "pause",
            {"id": "w1", "paused_sessions": []},
            "paused w1; kraft item resume w1 carries on",
        ),
        (
            ["item", "escalate", "w1", "--message", "help"],
            "escalate",
            {"id": "w1", "status": "escalating"},
            "asked an agent about w1's stop; kraft view show w1 follows it",
        ),
        (
            ["item", "set-chain", "w1", "--chain", "quick-task"],
            "set_chain",
            {"id": "w1", "chain_template": "quick-task"},
            "w1 now runs the quick-task chain",
        ),
    ],
    ids=[
        "resume",
        "start",
        "retry",
        "retry-path",
        "raise-budget",
        "raise-budget-no-cap",
        "pause",
        "pause-nothing-running",
        "escalate",
        "set-chain",
    ],
)
def test_a_small_answer_reads_as_one_line_too(monkeypatch, capsys, argv, fn, answer, said):
    """resume, retry and raise-budget usually answer a handful of keys, not
    the row: they printed those as a block, and raise-budget never said the
    new cap."""

    async def fake(*_a, **_kw):
        return answer

    monkeypatch.setattr(cli.item.client, fn, fake)
    cli.main(argv)
    assert capsys.readouterr().out == said + "\n"
    cli.main([*argv, "--json"])
    assert json.loads(capsys.readouterr().out) == answer


@pytest.mark.parametrize(
    ("item", "said"),
    [
        ({"pending_gate": "plan_approval", "current_node_id": "plan_approval"}, "plan_approval"),
        ({"pending_gate": None, "current_node_id": "verify"}, "verify"),
    ],
    ids=["gate", "node"],
)
def test_skip_names_what_it_skipped(monkeypatch, capsys, item, said):
    """It printed "skipped on <id>": the pending gate, else the current node,
    as the server picks it."""

    async def get_work_item(item_id=None, **_):
        return {"id": item_id, **item}

    async def skip(*_a, **_kw):
        return dict(_ROW)

    monkeypatch.setattr(cli.item.client, "get_work_item", get_work_item)
    monkeypatch.setattr(cli.item.client, "skip", skip)
    cli.main(["item", "skip", "w1"])
    assert capsys.readouterr().out == f"skipped {said} on w1; the item is now running\n"


def test_reject_asks_for_its_note_before_it_looks_for_a_gate(monkeypatch, capsys):
    """A blank note said "no gate is pending" on an item with none, before
    the note it was missing."""

    async def get(path, **_):
        return {"id": "w1", "pending_gate": None}

    monkeypatch.setattr("kraft.client.transport._get", get)
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "reject", "w1", "--note", "  "])
    assert caught.value.code == 1
    assert "a reject note is required" in capsys.readouterr().err


_PREVIEW = {
    "freed_bytes": 1024**3,
    "used_after_bytes": 9 * 1024**3,
    "state_after": "ok",
    "items": [
        {
            "id": "w1",
            "title": "Fix",
            "bytes": 1024**3,
            "archivable": True,
            "refusal": None,
            "uncommitted_files": 3,
            "unpushed_commits": 0,
            "branch_kept": False,
        },
    ],
}


@pytest.fixture
def archiving(monkeypatch):
    """The client calls `item archive` makes, recorded: (previewed, archived) id lists."""
    calls = {"previewed": [], "archived": [], "preview": _PREVIEW, "bulk": None}

    async def storage_preview(ids):
        calls["previewed"].append(ids)
        return calls["preview"]

    async def archive(ids):
        calls["archived"].append(ids)
        if calls["bulk"] is not None:
            return calls["bulk"]
        return {"results": [{"id": i, "ok": True, "status": "completed"} for i in ids]}

    monkeypatch.setattr(cli.item.client, "storage_preview", storage_preview)
    monkeypatch.setattr(cli.item.client, "archive", archive)
    return calls


def _archived(wid) -> bool:
    return bool(asyncio.run(cli.item.client.transport._get(f"/work-items/{wid}"))["archived_at"])


def test_archive_needs_a_yes_and_then_archives(app, repo, make_item, capsys):
    wid = make_item(repo)
    _set_status(wid, "completed")

    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "archive", wid])

    assert stopped.value.code == 1
    seen = capsys.readouterr()
    assert wid in seen.out and "archiving frees" in seen.out
    assert "pass --yes" in seen.err
    assert not _archived(wid)

    cli.main(["item", "archive", wid, "--yes"])

    assert f"archived {wid}" in capsys.readouterr().out
    assert _archived(wid)

    cli.main(["item", "restore", wid])

    assert f"restored {wid}" in capsys.readouterr().out
    assert not _archived(wid)


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["human", "json"])
def test_archive_with_yes_archives_every_id_and_prints_each_result(archiving, capsys, flags):
    cli.main(["item", "archive", "w1", "w2", "--yes", *flags])

    out = capsys.readouterr().out
    assert archiving["archived"] == [["w1", "w2"]]
    if flags:
        assert json.loads(out) == {
            "results": [
                {"id": "w1", "ok": True, "status": "completed"},
                {"id": "w2", "ok": True, "status": "completed"},
            ]
        }
    else:
        assert out.splitlines()[-2:] == ["archived w1", "archived w2"]


@pytest.mark.parametrize(
    ("bulk", "refused", "sent", "lines"),
    [
        (
            {
                "results": [
                    {"id": "w1", "ok": False, "error": "git said no"},
                    {"id": "w2", "ok": True},
                ]
            },
            None,
            ["w1", "w2"],
            ["not archived w1: git said no", "archived w2"],
        ),
        (None, "w2", ["w1"], ["archived w1", "not archived w2: no"]),
        (None, "w1", ["w2"], ["not archived w1: no", "archived w2"]),
    ],
    ids=["bulk-refuses-one", "preview-refuses-one", "preview-refuses-first"],
)
def test_archive_exits_1_after_printing_when_an_id_was_not_archived(
    archiving, capsys, bulk, refused, sent, lines
):
    archiving["bulk"] = bulk
    if refused:
        archiving["preview"] = {
            **_PREVIEW,
            "items": [
                _PREVIEW["items"][0] | {"id": wid, "archivable": wid != refused, "refusal": "no"}
                for wid in ("w1", "w2")
            ],
        }

    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "archive", "w1", "w2", "--yes"])

    assert stopped.value.code == 1
    assert archiving["archived"] == [sent]
    assert capsys.readouterr().out.splitlines()[-2:] == lines


def test_archive_sends_nothing_when_the_preview_refused_every_id(archiving, capsys):
    archiving["preview"] = {
        **_PREVIEW,
        "items": [_PREVIEW["items"][0] | {"archivable": False, "refusal": "already archived"}],
    }

    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "archive", "w1", "--yes"])

    assert stopped.value.code == 1
    assert archiving["archived"] == []
    assert "not archived w1: already archived" in capsys.readouterr().out


def test_archive_json_without_yes_prints_the_preview_and_refuses(archiving, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "archive", "w1", "--json"])

    assert stopped.value.code == 1
    assert json.loads(capsys.readouterr().out) == _PREVIEW
    assert archiving["archived"] == []


@pytest.mark.parametrize(
    ("items", "taken"),
    [
        ([{"id": "a", "reclaimable": True}, {"id": "b", "reclaimable": False}], [["a"]]),
        ([{"id": "b", "reclaimable": False}], []),
    ],
    ids=["some", "none"],
)
def test_archive_reclaimable_takes_what_the_storage_view_marks(
    monkeypatch, archiving, capsys, items, taken
):
    async def storage_usage():
        return {"items": items}

    monkeypatch.setattr(cli.item.client, "storage_usage", storage_usage)

    cli.main(["item", "archive", "--reclaimable", "--yes"])

    assert archiving["archived"] == taken
    assert archiving["previewed"] == taken
    if not taken:
        assert capsys.readouterr().out.strip() == "nothing to archive"


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["human", "json"])
def test_archive_reclaimable_takes_the_200_largest_and_says_so(
    monkeypatch, archiving, capsys, flags
):
    async def storage_usage():  # `GET /storage` lists largest first
        return {"items": [{"id": f"w{n}", "reclaimable": True} for n in range(201)]}

    monkeypatch.setattr(cli.item.client, "storage_usage", storage_usage)
    sentence = "archived the 200 largest of 201 reclaimable items; run it again for the rest"

    cli.main(["item", "archive", "--reclaimable", "--yes", *flags])

    out, err = capsys.readouterr()
    assert archiving["previewed"] == archiving["archived"] == [[f"w{n}" for n in range(200)]]
    if flags:
        assert sentence not in out
        assert err.strip() == sentence
    else:
        assert out.splitlines()[-1] == sentence


def test_archive_reclaimable_refuses_ids_beside_it(archiving, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "archive", "w1", "--reclaimable", "--yes"])

    assert stopped.value.code == 1
    assert "not both" in capsys.readouterr().err
    assert archiving["archived"] == []


def test_archive_with_no_id_takes_the_item_you_are_standing_in(monkeypatch, archiving):
    monkeypatch.setattr("kraft.client.context.resolve_context", lambda cwd=None: ("w9", "user"))

    cli.main(["item", "archive", "--yes", "--json"])

    assert archiving["archived"] == [["w9"]]


def test_archive_refuses_a_workers_own_item(monkeypatch, archiving, capsys):
    monkeypatch.setattr("kraft.client.context.resolve_context", lambda cwd=None: ("w9", "worker"))

    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "archive", "w9", "--yes"])

    assert stopped.value.code == 1
    assert "cannot act on its own work item" in capsys.readouterr().err
    assert archiving["archived"] == []


@pytest.mark.parametrize(("argv", "sent"), [(["w1"], "w1"), ([], None)], ids=["id", "no-id"])
def test_restore_names_the_item(monkeypatch, capsys, argv, sent):
    seen = []

    async def restore(work_item_id=None):
        seen.append(work_item_id)
        return {"id": "w1", "status": "completed"}

    monkeypatch.setattr(cli.item.client, "restore", restore)

    cli.main(["item", "restore", *argv])

    assert seen == [sent]
    assert "restored w1" in capsys.readouterr().out
