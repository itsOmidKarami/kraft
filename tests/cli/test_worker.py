"""`kraft admin worker`: the verbs a person types."""

from __future__ import annotations

import json

import pytest

from kraft import cli, paths
from kraft.config import read_yaml


def _run(capsys, *argv):
    try:
        cli.main(["admin", "worker", *argv])
        code = 0
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return (code if isinstance(code, int) else 1), out.out + out.err


def test_add_prints_the_token_once_and_the_command_to_run_on_the_machine(app, capsys):
    code, out = _run(capsys, "add", "home", "--capacity", "2")
    assert code == 0
    token = next(w for w in out.split() if w.startswith("kwt_"))
    assert (
        "kraft admin worker serve --server" in out and out.count(token) == 2
    )  # the line and the command
    code, listing = _run(capsys, "list")
    assert (
        code == 0
        and "home" in listing
        and "dial-in" in listing
        and "offline: never connected" in listing
    )
    assert token not in listing


def test_list_json_is_the_raw_payload(app, capsys):
    _run(capsys, "add", "home")
    code, out = _run(capsys, "list", "--json")
    assert code == 0 and [w["name"] for w in json.loads(out)["workers"]] == ["local", "home"]


def test_list_with_no_machines_shows_only_the_local_worker(app, capsys):
    code, out = _run(capsys, "list")
    (_header, row) = out.splitlines()
    assert code == 0 and row.split()[:8] == [
        "local",
        "this",
        "machine",
        "online",
        "0",
        "·",
        "no",
        "limit",
    ]


def test_remove_forgets_the_machine(app, capsys):
    _run(capsys, "add", "home")
    assert _run(capsys, "remove", "home")[0] == 0
    assert "home" not in _run(capsys, "list")[1]
    code, out = _run(capsys, "remove", "home")
    assert code == 1 and "home" in out


def test_add_with_a_bad_name_exits_1_and_says_why(app, capsys):
    code, out = _run(capsys, "add", "any")
    assert code == 1 and "reserved" in out


def test_add_with_an_ssh_host_that_is_an_option_exits_1_and_says_why(app, capsys):
    code, out = _run(capsys, "add", "boxed", "--ssh=-oProxyCommand")
    assert code == 1 and "ssh" in out


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("--server", "http://h"),
        ("--token", "t"),
        ("--stdio", "--server", "http://h", "--token", "t"),
    ],
    ids=["neither", "server-without-token", "token-without-server", "both-modes"],
)
def test_serve_needs_exactly_one_connection_form(capsys, argv):
    code, out = _run(capsys, "serve", *argv)
    assert code != 0 and ("--stdio" in out or "--server" in out)


def test_check_exits_1_when_a_row_fails(monkeypatch, capsys):
    async def rows(**_):
        return [{"name": "git", "ok": False, "detail": "missing", "skipped": False, "warn": False}]

    monkeypatch.setattr("kraft.remote.check.run", rows)
    code, out = _run(capsys, "check")
    assert code == 1 and "git" in out


def test_attach_is_listed_as_run_by_kraft_and_takes_the_session_it_carries(capsys):
    with pytest.raises(SystemExit):
        cli.main(["admin", "worker", "--help"])
    assert "run by Kraft, not by hand" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["admin", "worker", "attach", "--help"])
    out = capsys.readouterr().out
    assert "--run-dir" in out and "--item" in out and "--session" in out


@pytest.mark.parametrize(
    ("row", "slots"),
    [
        ({"slots_used": 1, "capacity": 2, "capacity_source": "machine"}, "1 of 2 (machine)"),
        ({"slots_used": 0, "capacity": 3, "capacity_source": "server"}, "0 of 3"),
        ({"slots_used": 0, "capacity": 1, "capacity_source": "default"}, "0 of 1"),
        ({"slots_used": 2, "capacity": None}, "2 · no limit"),
    ],
    ids=["machine-lower", "server", "default", "local-no-limit"],
)
def test_the_slots_column_says_who_set_the_limit(row, slots):
    from kraft.cli import worker

    assert worker._slots(row) == slots


def test_add_without_a_capacity_writes_none(app, capsys):
    assert _run(capsys, "add", "home")[0] == 0
    assert "capacity" not in (paths.config_dir() / "workers.yaml").read_text()


def test_add_writes_each_group_and_list_shows_them(app, capsys):
    code, _ = _run(capsys, "add", "gpu-2", "--group", "gpu", "--group", "linux")
    assert code == 0
    assert read_yaml(paths.config_dir() / "workers.yaml")["workers"]["gpu-2"]["groups"] == [
        "gpu",
        "linux",
    ]
    _, listing = _run(capsys, "list")
    header, _local, row = listing.splitlines()
    assert "GROUPS" in header and "gpu,linux" in row


@pytest.fixture
def edits(monkeypatch):
    """The `(name, fields)` each `edit_worker` call sends: the `app` fixture's real server would
    hide what the CLI put on the wire."""
    sent = []

    async def edit(name, fields):
        sent.append((name, fields))
        return {"worker": {"name": name}}

    monkeypatch.setattr("kraft.client.edit_worker", edit)
    return sent


@pytest.mark.parametrize(
    ("argv", "fields"),
    [
        (("edit", "gpu", "--capacity", "3"), {"capacity": 3}),
        (("edit", "gpu", "--capacity", "none"), {"capacity": None}),
        (("edit", "gpu", "--give-up-after", "never"), {"give_up_after": "never"}),
        (("edit", "gpu", "--group", "a", "--group", "b"), {"groups": ["a", "b"]}),
        (("edit", "gpu", "--no-groups"), {"groups": []}),
        (("edit", "gpu", "--repos", "a,b"), {"repos": ["a", "b"]}),
        (("edit", "gpu", "--every-repo"), {"repos": None}),
        (("edit", "gpu", "--ssh", "box"), {"ssh": "box"}),
        (("edit", "gpu", "--capacity", "2", "--ssh", "box"), {"capacity": 2, "ssh": "box"}),
        (("enable", "gpu"), {"enabled": True}),
        (("disable", "gpu"), {"enabled": False}),
    ],
    ids=[
        "capacity",
        "capacity-none",
        "give-up-after",
        "groups",
        "no-groups",
        "repos",
        "every-repo",
        "ssh",
        "two-fields",
        "enable",
        "disable",
    ],
)
def test_each_verb_sends_only_the_fields_it_was_given(edits, capsys, argv, fields):
    code, _ = _run(capsys, *argv)
    assert code == 0 and edits == [("gpu", fields)]


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (("edit", "gpu"), "nothing to change"),
        (("edit", "gpu", "--group", "a", "--no-groups"), "not allowed with"),
        (("edit", "gpu", "--repos", "a", "--every-repo"), "not allowed with"),
        (("edit", "gpu", "--capacity", "many"), "not a number"),
    ],
    ids=["nothing", "group-and-no-groups", "repos-and-every-repo", "capacity-word"],
)
def test_an_edit_the_parser_can_judge_exits_2_and_sends_nothing(edits, capsys, argv, says):
    code, out = _run(capsys, *argv)
    assert code == 2 and says in out and edits == []


def test_an_edit_the_server_refuses_prints_its_words_and_exits_1(app, capsys):
    _run(capsys, "add", "home")
    code, out = _run(capsys, "edit", "home", "--capacity", "0")
    assert code == 1 and "capacity" in out
    code, out = _run(capsys, "edit", "nobody", "--capacity", "2")
    assert code == 1 and "no worker named 'nobody'" in out


def test_disable_and_enable_show_in_the_list_and_the_file(app, capsys):
    _run(capsys, "add", "home")
    code, out = _run(capsys, "disable", "home")
    assert code == 0 and "home is disabled" in out
    assert read_yaml(paths.config_dir() / "workers.yaml")["workers"]["home"]["enabled"] is False
    _, listing = _run(capsys, "list")
    assert "disabled · offline: never connected" in listing
    assert _run(capsys, "enable", "home")[0] == 0
    _, listing = _run(capsys, "list")
    assert "disabled" not in listing and "offline: never connected" in listing


def test_add_disabled_registers_a_machine_that_takes_no_steps(app, capsys):
    assert _run(capsys, "add", "home", "--disabled")[0] == 0
    assert read_yaml(paths.config_dir() / "workers.yaml")["workers"]["home"]["enabled"] is False


def test_rotate_token_prints_the_new_token_once_with_the_command(app, capsys):
    _, added = _run(capsys, "add", "home")
    old = next(w for w in added.split() if w.startswith("kwt_"))
    code, out = _run(capsys, "rotate-token", "home")
    new = next(w for w in out.split() if w.startswith("kwt_"))
    assert code == 0 and new != old and out.count(new) == 2 and old not in out
    assert "kraft admin worker serve --server" in out
    assert new not in _run(capsys, "list")[1]


def test_rotate_token_on_an_ssh_machine_exits_1_with_the_servers_words(app, capsys):
    _run(capsys, "add", "boxed", "--ssh", "no-such-host.invalid")
    code, out = _run(capsys, "rotate-token", "boxed")
    assert code == 1 and "connects over ssh" in out


@pytest.mark.parametrize(
    ("row", "state"),
    [
        ({"online": True}, "online"),
        ({"online": False, "reason": "ssh: down"}, "offline: ssh: down"),
        ({"online": True, "enabled": False}, "disabled"),
        (
            {"online": False, "reason": "ssh: down", "enabled": False},
            "disabled · offline: ssh: down",
        ),
    ],
    ids=["online", "offline", "disabled", "disabled-offline"],
)
def test_the_state_column_puts_disabled_before_the_offline_reason(row, state):
    from kraft.cli import worker

    assert worker._state(row) == state
