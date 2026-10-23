"""`kraft admin worker`: the verbs a person types."""

from __future__ import annotations

import json

import pytest

from kraft import cli


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
    assert code == 0 and json.loads(out)["workers"][0]["name"] == "home"


def test_list_with_no_machines_says_so(app, capsys):
    assert _run(capsys, "list") == (0, "no workers\n")


def test_remove_forgets_the_machine(app, capsys):
    _run(capsys, "add", "home")
    assert _run(capsys, "remove", "home")[0] == 0
    assert _run(capsys, "list")[1] == "no workers\n"
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
