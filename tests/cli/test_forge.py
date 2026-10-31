"""`kraft admin forge`: the verbs a person types."""

from __future__ import annotations

import io
import json
import sys

import pytest

from kraft import cli, client
from kraft.client import transport
from kraft.forges import ForgesFile

ACCT = {"forge": "gitlab", "web_url": "https://code.corp.example", "default_for_host": True}


def _run(capsys, *argv):
    said = ""
    try:
        cli.main(["admin", "forge", *argv])
        code = 0
    except SystemExit as exc:
        code = exc.code
        said = code if isinstance(code, str) else ""  # what the interpreter would print
    out = capsys.readouterr()
    return (code if isinstance(code, int) else 1), out.out + out.err + said


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
            {
                "api_url": None,
                "default_for_host": True,
                "client": "cli",
                "ca_file": None,
                "token": None,
            },
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


TOK = "ghp_FAKEPASTED0123456789"  # a made-up string
URL = "https://code.corp.example"
ADD = ["add", "corp", "--forge", "gitlab", "--web-url", URL]


def _both(capsys, monkeypatch, flags):
    """`add` and `edit` with the same flags: what each sent, and how each ended."""
    sent = {
        "add": _stub(monkeypatch, "add_forge", ACCT),
        "edit": _stub(monkeypatch, "edit_forge", ACCT),
    }
    ended = {"add": _run(capsys, *ADD, *flags)}
    ended["edit"] = _run(capsys, "edit", "corp", *flags)
    return sent, ended


@pytest.mark.parametrize(
    ("flags", "source"),
    [
        (["--token-from", "env:CORP_GL", "--kind", "pat"], {"from": "env", "env": "CORP_GL"}),
        (["--token-from", "file:~/t"], {"from": "file", "file": "~/t"}),
        (["--token-from", "git-credential"], {"from": "git-credential"}),
        (["--client", "cli", "--token-from", "env:CORP_GL"], None),
        (["--token-from", "env:" + TOK], None),
        (["--token-from", TOK], None),
    ],
    ids=[
        "env:CORP_GL",
        "file:~/t",
        "git-credential",
        "client-cli-with-token-from-errors",
        "token-pasted-as-a-variable-name",
        "token-pasted-as-a-source",
    ],
)
def test_add_and_edit_send_the_source(capsys, monkeypatch, flags, source):
    sent, ended = _both(capsys, monkeypatch, flags)
    kind = flags[flags.index("--kind") + 1] if "--kind" in flags else "unknown"
    for verb, (code, out) in ended.items():
        assert TOK not in out  # an error never quotes what was refused
        if source is None:
            assert code == 1 and "error:" in out and not sent[verb]
            continue
        assert code == 0
        token = {**source, "kind": kind}
        if verb == "add":
            assert sent[verb] == [
                (
                    ("corp", "gitlab", URL),
                    {
                        "api_url": None,
                        "default_for_host": False,
                        "client": "api",
                        "ca_file": None,
                        "token": token,
                    },
                )
            ]
        else:
            assert sent[verb] == [(("corp", {"client": "api", "token": token}), {})]


@pytest.mark.parametrize(
    ("verb", "put_fails", "remove_fails", "tty"),
    [
        ("add", False, False, False),
        ("add", True, False, False),
        ("add", True, True, False),
        ("edit", False, False, False),
        ("edit", True, False, False),
        ("add", False, False, True),
    ],
    ids=[
        "add-pasted",
        "add-put-fails-removes-account",
        "add-put-and-remove-fail-keeps-the-first-error",
        "edit-pasted",
        "edit-put-fails-puts-the-old-fields-back",
        "terminal-is-not-echoed",
    ],
)
def test_a_token_is_read_from_stdin_never_argv(
    capsys, monkeypatch, verb, put_fails, remove_fails, tty
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(TOK + "\n"))
    if tty:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        # StringIO would give the token away through read(): only getpass may supply it.
        monkeypatch.setattr(sys.stdin, "read", lambda: "")
        monkeypatch.setattr("getpass.getpass", lambda prompt="": TOK)
    sent = _stub(monkeypatch, "add_forge" if verb == "add" else "edit_forge", ACCT)
    _stub(monkeypatch, "list_forges", {"accounts": {"corp": {"client": "cli", "ca_file": None}}})
    removed = _stub(monkeypatch, "remove_forge", None)
    pasted = []

    async def paste(*args):
        pasted.append(args)
        if put_fails:
            raise transport.ClientError("kraft: too short", kind=transport.Failure.SERVER_ERROR)
        return ACCT

    async def cannot_remove(*args):
        raise transport.ClientError("kraft: gone", kind=transport.Failure.UNREACHABLE)

    monkeypatch.setattr(client, "set_forge_token", paste)
    if remove_fails:
        monkeypatch.setattr(client, "remove_forge", cannot_remove)
    argv = [*(ADD if verb == "add" else ["edit", "corp"]), "--token-stdin", "--kind", "pat"]
    code, out = _run(capsys, *argv)
    # The token reaches `set_forge_token` and nothing else: not argv, the payload, or stdout.
    assert TOK not in " ".join(argv) and TOK not in out and TOK not in repr(sent)
    assert pasted == [("corp", TOK, "pat")]
    assert sent[0][1].get("token") is None
    if verb == "edit":  # a failed paste puts a cli account back, never left as api
        assert [a[0][1] for a in sent] == [{"client": "api"}] + (
            [{"client": "cli"}] if put_fails else []
        )
    assert code == (1 if put_fails else 0) and ("too short" in out) is put_fails
    assert removed == (
        [(("corp",), {})] if verb == "add" and put_fails and not remove_fails else []
    )


def test_edit_kind_without_a_token_flag_is_refused_not_ignored(capsys, monkeypatch):
    sent = _stub(monkeypatch, "edit_forge", ACCT)
    code, out = _run(capsys, "edit", "corp", "--api-url", URL + "/api/v4", "--kind", "pat")
    assert code == 1 and "--kind" in out and "--token-from" in out and not sent


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """forges.yaml in a config dir of its own, and a server that reloads it."""
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    calls = {"reload": 0, "answer": {}, "sees": True}

    async def health():
        return {}

    async def reload():
        calls["reload"] += 1
        if calls.get("boom") and calls["reload"] == 1:
            raise transport.ClientError("kraft: down", kind=transport.Failure.UNREACHABLE)
        return calls["answer"]

    async def listing():
        # What the server reads: this file, or (sees False) another config dir's.
        if not calls["sees"]:
            return {"accounts": {}}
        accounts = ForgesFile.load(tmp_path / "forges.yaml").accounts
        return {
            "accounts": {
                n: {"token": a.token and {"from": a.token.from_}} for n, a in accounts.items()
            }
        }

    monkeypatch.setattr(client, "health", health)
    monkeypatch.setattr(client, "reload_templates", reload)
    monkeypatch.setattr(client, "list_forges", listing)
    return tmp_path / "forges.yaml", calls


def _other_instance():
    async def health():
        raise transport.ClientError("other", kind=transport.Failure.OTHER_INSTANCE)

    return health


@pytest.mark.parametrize(
    ("where", "code", "text", "written"),
    [
        ("same-run-dir", 0, "added corp", True),
        ("other-run-dir", 1, "written on the server's machine", False),
        ("in-a-worker", 1, "a worker does not set it", False),
        ("reload-refused-restores", 1, "refused forges.yaml", False),
        ("another-refusal-is-not-ours", 0, "added corp", True),
        ("reload-raises-restores", 1, "kraft: down", False),
        ("server-reads-another-dir", 1, "does not read", False),
    ],
    ids=[
        "same-run-dir",
        "other-run-dir",
        "in-a-worker",
        "reload-refused-restores",
        "another-refusal-is-not-ours",
        "reload-raises-restores",
        "server-reads-another-dir",
    ],
)
def test_a_command_source_is_written_on_the_servers_machine(
    capsys, monkeypatch, disk, where, code, text, written
):
    path, calls = disk
    path.write_text("# mine\naccounts: {}\n")
    if where == "other-run-dir":
        monkeypatch.setattr(client, "health", _other_instance())
    if where == "in-a-worker":
        monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    if where == "reload-refused-restores":
        calls["answer"] = {"refused_forges": "token: nope"}
    if where == "another-refusal-is-not-ours":
        calls["answer"] = {"invalid_templates": ["x"], "refused_policy": None}
    if where == "reload-raises-restores":
        calls["boom"] = True
    if where == "server-reads-another-dir":
        calls["sees"] = False
    http = _stub(monkeypatch, "add_forge", ACCT)  # a command source never goes over HTTP
    got, out = _run(capsys, *ADD, "--token-from", "command:sh -c 'echo x'")
    assert got == code and text in out and not http
    if written:
        token = ForgesFile.load(path).accounts["corp"].token
        assert token.from_ == "command" and token.command == "sh -c 'echo x'"
        assert calls["reload"] == 1
    else:
        assert path.read_text() == "# mine\naccounts: {}\n"
        assert calls["reload"] == (0 if where in ("other-run-dir", "in-a-worker") else 2)
