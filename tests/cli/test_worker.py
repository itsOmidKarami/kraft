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
