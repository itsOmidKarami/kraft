"""`kraft repo connect` on the API path: an account whose token is asked for, tested, and kept
only when it works."""

from __future__ import annotations

import asyncio
import io

import pytest
import yaml
from support.connect import _accounts, _forges_yaml, _origin, _probes, _prompts, _spy

from kraft import cli, client

# `app` and `repo` fixtures: tests/conftest.py.

_TOKEN = "fake-token-0123456789-abcdef"
_GITHUB = "https://github.com/acme/app.git"
_SELF_HOSTED = "git@code.corp.example:gitlab/team/app.git"
_CORP = ["--forge", "gitlab", "--web-url", "https://code.corp.example"]
_WRITES = ("add_forge", "edit_forge", "set_forge_token", "remove_forge")


def _kept():
    """The accounts forges.yaml holds now: none when it is gone or empty."""
    return (
        (yaml.safe_load(_forges_yaml().read_text()) or {}).get("accounts") or {}
        if _forges_yaml().exists()
        else {}
    )


def _connect(repo, *flags):
    cli.main(["repo", "connect", str(repo), *flags])


def _never_leaked(calls, output, token=_TOKEN):
    """The token reached `set_forge_token` and nowhere else: no payload, no output."""
    for name, a, kw in calls:
        if name != "set_forge_token":
            assert token not in repr((a, kw)), name
    assert token not in output


@pytest.mark.parametrize(
    "probes, answers, flags, expect",
    [
        pytest.param(lambda a: "ok", [], [], {"asked": 0, "pasted": False}, id="git-has-one"),
        pytest.param(
            lambda a: "ok" if a.token else "no_token",
            ["1"],
            [],
            {"asked": 1, "pasted": True},
            id="paste",
        ),
        pytest.param(
            lambda a: "no_token",
            ["2", "MY_GH_TOKEN"],
            [],
            {"asked": 2, "env": "MY_GH_TOKEN", "said": "the server reads MY_GH_TOKEN once"},
            id="env",
        ),
        pytest.param(
            lambda a: "no_token",
            ["2", "ghp_" + "a1" * 18, "MY_GH_TOKEN"],
            [],
            {"asked": 3, "env": "MY_GH_TOKEN", "said": "looks like a token"},
            id="env-refuses-a-pasted-token",
        ),
        pytest.param(
            lambda a: "ok" if a.token else "no_token",
            ["3", "op read op://dev/gh"],
            [],
            {"asked": 2, "command": "op read op://dev/gh"},
            id="command",
        ),
        pytest.param(
            lambda a: "no_token",
            [],
            ["-y", "--forge", "github"],
            {"fails": True},
            id="y-without-a-token",
        ),
    ],
)
def test_an_api_account_asks_where_the_token_comes_from_only_when_git_has_none(
    app, capsys, repo, monkeypatch, probes, answers, flags, expect
):
    _origin(repo, _GITHUB)
    _probes(monkeypatch, probes)
    seen = _prompts(monkeypatch, answers)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": seen.append(prompt) or _TOKEN)
    real_edit = client.edit_forge

    async def guarded(name, fields):
        assert "ghp_" not in repr(fields), "a token-shaped variable name reached the server"
        return await real_edit(name, fields)

    monkeypatch.setattr(client, "edit_forge", guarded)
    calls = _spy(monkeypatch, *_WRITES)
    if expect.get("fails"):
        with pytest.raises(SystemExit):
            _connect(repo, "--client", "api", *flags)
        assert ("remove_forge", ("github.com",), {}) in calls
        assert _kept() == {}
        assert asyncio.run(client.repos()) == []
        return
    _connect(repo, "--client", "api", *flags)
    out = capsys.readouterr().out
    assert "signed in as canned" in out or expect.get("env") or expect.get("command")
    assert expect.get("said", "") in out
    assert asyncio.run(client.repos())[0]["forge"] == "github"
    # The questions: the source, then (token | variable name, + a refused one | command).
    assert sum(
        1 for p in seen if p.startswith(("  git has no", "  token", "  var", "  command"))
    ) == (expect["asked"] if not expect.get("pasted") else 2)
    pasted = [c for c in calls if c[0] == "set_forge_token"]
    assert [c[1] for c in pasted] == (
        [("github.com", _TOKEN, "unknown")] if expect.get("pasted") else []
    )
    if env := expect.get("env"):
        [edit] = [c for c in calls if c[0] == "edit_forge"]
        assert edit[1] == ("github.com", {"token": {"from": "env", "env": env, "kind": "unknown"}})
    if command := expect.get("command"):
        assert _accounts()["github.com"]["token"]["command"] == command
    _never_leaked(calls, out + capsys.readouterr().err)
    assert "ghp_" not in yaml.safe_dump(_accounts())


@pytest.mark.parametrize(
    "probes, answers, patched",
    [
        pytest.param(
            lambda a: "ok" if a.api_url else "unreachable",
            ["https://code.corp.example/gitlab/api/v4"],
            True,
            id="unreachable-asks-then-patches",
        ),
        pytest.param(lambda a: "ok", [], False, id="ok-never-asks"),
    ],
)
def test_connect_asks_the_api_address_only_when_the_derived_one_does_not_answer(
    app, capsys, repo, monkeypatch, probes, answers, patched
):
    _origin(repo, _SELF_HOSTED)
    _probes(monkeypatch, probes)
    seen = _prompts(monkeypatch, answers)
    calls = _spy(monkeypatch, "edit_forge")
    _connect(repo, "--client", "api", *_CORP)
    derived = "https://code.corp.example/api/v4"
    assert seen == (
        [f"  the API did not answer at {derived}. API address [{derived}] "] if patched else []
    )
    assert [c[1] for c in calls] == (
        [("code.corp.example", {"api_url": answers[0]})] if patched else []
    )
    assert asyncio.run(client.repos())[0]["forge"] == "gitlab"


@pytest.mark.parametrize(
    "answers, flags, kept",
    [
        pytest.param(["n"], [], False, id="no-removes"),
        pytest.param(["y"], [], True, id="yes-keeps"),
        pytest.param([], ["-y"], False, id="y-removes-and-fails"),
    ],
)
def test_a_failed_token_test_keeps_the_account_only_on_a_yes(
    app, capsys, repo, monkeypatch, answers, flags, kept
):
    _origin(repo, _SELF_HOSTED)
    _probes(monkeypatch, lambda a: "refused")
    seen = _prompts(monkeypatch, answers)
    calls = _spy(monkeypatch, "remove_forge")
    args = ["--client", "api", "--token-from", "env:CORP_TOKEN", *_CORP, *flags]
    if kept:
        _connect(repo, *args)
        assert list(_accounts()) == ["code.corp.example"] and calls == []
        assert asyncio.run(client.repos())[0]["forge"] == "gitlab"
    else:
        with pytest.raises(SystemExit) as caught:
            _connect(repo, *args)
        assert "did not pass its test" in str(caught.value)
        assert [c[1] for c in calls] == [("code.corp.example",)]
        assert asyncio.run(client.repos()) == [] and _kept() == {}
    assert seen == ([] if flags else ["  keep forge account code.corp.example anyway? [y/N] "])


@pytest.mark.parametrize("flags", [[], ["--client", "api", "-y"]], ids=["plain", "client-api-y"])
@pytest.mark.parametrize("state", ["ok", "refused"])
def test_a_host_with_an_api_account_shows_its_test_and_asks_nothing(
    app, capsys, repo, monkeypatch, state, flags
):
    """Connect never fails on an account that is already there."""
    _origin(repo, _SELF_HOSTED)
    asyncio.run(
        client.add_forge("corp", "gitlab", "https://code.corp.example", None, True, client="api")
    )
    _probes(monkeypatch, lambda a: state)
    seen = _prompts(monkeypatch, [])
    calls = _spy(monkeypatch, "test_forge", *_WRITES)
    _connect(repo, *flags)  # -y with the API flag: the account is there, nothing to ask
    out = capsys.readouterr().out
    assert seen == [] and [c[0] for c in calls] == ["test_forge"]
    assert ("signed in as canned" in out) == (state == "ok") and f"{state} detail" in out
    assert asyncio.run(client.repos())[0]["forge"] == "gitlab"


@pytest.mark.parametrize(
    "remote, before, flags, interactive, said",
    [
        pytest.param(_GITHUB, {}, [], False, "no terminal to ask: pass --forge", id="no-terminal"),
        pytest.param(
            _SELF_HOSTED,
            {"code.corp.example": {"forge": "gitlab", "web_url": "https://code.corp.example"}},
            ["--forge", "gitlab"],
            False,
            "kraft admin forge edit code.corp.example --client api",
            id="client-api-on-a-cli-account",
        ),
        pytest.param(
            _GITHUB, {}, ["--token-from", "env:GH_ONE"], True, None, id="token-from-implies-api"
        ),
    ],
)
def test_the_api_path_refuses_what_it_cannot_do(
    app, capsys, repo, monkeypatch, remote, before, flags, interactive, said
):
    _origin(repo, remote)
    for name, acct in before.items():
        asyncio.run(client.add_forge(name, **acct))
    _probes(monkeypatch, lambda a: "ok")
    seen = _prompts(monkeypatch, []) if interactive else []
    args = [] if flags and flags[0] == "--token-from" else ["--client", "api"]
    if said is None:
        _connect(repo, *args, *flags)
        assert _accounts()["github.com"]["client"] == "api" and seen == []
        return
    with pytest.raises(SystemExit) as caught:
        _connect(repo, *args, *flags)
    assert said in str(caught.value) and asyncio.run(client.repos()) == []
    assert (_accounts() if _forges_yaml().exists() else {}) == before


def test_the_api_path_changes_nothing_for_a_repo_connected_already(app, capsys, repo, monkeypatch):
    _origin(repo, _GITHUB)
    _connect(repo, "-y")
    capsys.readouterr()
    calls = _spy(monkeypatch, *_WRITES)
    _connect(repo, "--client", "api", "-y", "--forge", "github")
    assert "is connected already" in capsys.readouterr().out
    assert calls == [] and not _forges_yaml().exists()


def test_json_on_the_api_path_asks_nothing(app, capsys, repo, monkeypatch):
    _origin(repo, _GITHUB)
    _probes(monkeypatch, lambda a: "ok")
    seen = _prompts(monkeypatch, [])
    _connect(repo, "--client", "api", "--json", "--forge", "github")
    assert seen == [] and _accounts()["github.com"]["client"] == "api"


def test_a_token_on_stdin_reaches_only_set_forge_token(app, capsys, repo, monkeypatch):
    _origin(repo, _GITHUB)
    _probes(monkeypatch, lambda a: "ok")
    monkeypatch.setattr("sys.stdin", io.StringIO(_TOKEN + "\n"))
    calls = _spy(monkeypatch, *_WRITES)
    _connect(repo, "-y", "--forge", "github", "--token-stdin")
    assert [c[1] for c in calls if c[0] == "set_forge_token"] == [("github.com", _TOKEN, "unknown")]
    _never_leaked(calls, "".join(capsys.readouterr()))
    assert _TOKEN not in _forges_yaml().read_text()
