"""`kraft admin forge`: the verbs a person types."""

from __future__ import annotations

import json

import pytest

from kraft import cli, client

ACCT = {"forge": "gitlab", "web_url": "https://code.corp.example", "default_for_host": True}


def _run(capsys, *argv):
    try:
        cli.main(["admin", "forge", *argv])
        code = 0
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return (code if isinstance(code, int) else 1), out.out + out.err


def _stub(monkeypatch, name, answer):
    seen = []

    async def call(*args, **kwargs):
        seen.append((args, kwargs))
        return answer

    monkeypatch.setattr(client, name, call)
    return seen


def test_add_sends_the_flags_and_says_what_it_added(monkeypatch, capsys):
    seen = _stub(monkeypatch, "add_forge", ACCT)
    code, out = _run(
        capsys,
        "add",
        "corp",
        "--forge",
        "gitlab",
        "--web-url",
        "https://code.corp.example",
        "--default-for-host",
    )
    assert code == 0 and "added corp (gitlab, https://code.corp.example)" in out
    assert seen == [
        (
            ("corp", "gitlab", "https://code.corp.example"),
            {"api_url": None, "default_for_host": True},
        )
    ]


@pytest.mark.parametrize(
    ("accounts", "expected"),
    [
        ({}, "no forge accounts"),
        ({"corp": ACCT}, "corp  gitlab  https://code.corp.example  [default]"),
        (
            {"gh": {**ACCT, "forge": "github", "default_for_host": False}},
            "gh  github  https://code.corp.example",
        ),
    ],
    ids=["none", "default", "plain"],
)
def test_list_renders_a_line_per_account(monkeypatch, capsys, accounts, expected):
    _stub(monkeypatch, "list_forges", {"accounts": accounts})
    code, out = _run(capsys, "list")
    assert code == 0 and out.strip() == expected
    _, raw = _run(capsys, "list", "--json")
    assert json.loads(raw) == {"accounts": accounts}


def test_remove_calls_the_client(monkeypatch, capsys):
    seen = _stub(monkeypatch, "remove_forge", None)
    code, out = _run(capsys, "remove", "corp")
    assert code == 0 and "removed corp" in out and seen == [(("corp",), {})]


@pytest.mark.parametrize(
    ("ok", "code", "last"), [(True, 0, "ok"), (False, 1, "failed")], ids=["ok", "failed"]
)
def test_test_prints_the_output_and_exits_1_on_failure(monkeypatch, capsys, ok, code, last):
    _stub(monkeypatch, "test_forge", {"ok": ok, "output": "signed in?\n"})
    got, out = _run(capsys, "test", "corp")
    assert got == code and out.splitlines() == ["signed in?", last]
