"""`kraft item`'s own parser: what `kraft item --help` and `kraft item create
--help` say, where a reviewer could not tell what a verb or argument was, what
`create`'s flags default to, and what `kraft item create` prints."""

from __future__ import annotations

import json

import pytest

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
