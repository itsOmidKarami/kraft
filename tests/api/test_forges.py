"""`/api/forges`: naming forge accounts, and asking whether their CLI is signed in."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from support.harness import connect_repo

from kraft import redact
from kraft.adapters.forge import credentials, probe
from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError
from kraft.api.routes import forges as routes
from kraft.config import read_yaml
from kraft.forges import ForgesFile
from kraft.paths import kraft_home

GL = {"name": "corp", "forge": "gitlab", "web_url": "https://code.corp.example/"}
API = {**GL, "client": "api"}
TOK = "ghp_FAKEPASTED0123456789"  # a made-up string: no test holds a real token
COMMAND = {"from": "command", "command": "sh -c 'echo x'"}


def _file(client):
    return client.app.state.templates_dir / "forges.yaml"


def _pasted(name="corp") -> Path:
    return kraft_home() / "secrets" / name


@pytest.mark.parametrize(
    "token",
    [
        None,
        "pasted",
        {"from": "file", "file": "{elsewhere}", "kind": "pat"},
    ],
    ids=["plain", "remove-deletes-the-pasted-file", "remove-keeps-a-file-it-did-not-write"],
)
def test_add_list_remove_round_trip(client, tmp_path, token):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text(TOK)
    body = {**GL, "default_for_host": True}
    if isinstance(token, dict):
        body = {**API, "token": {**token, "file": str(elsewhere)}}
    elif token:
        body = API
    reply = client.post("/api/forges", json=body)
    assert reply.status_code == 201, reply.text
    if token == "pasted":
        assert _put(client, TOK).status_code == 200
        assert _pasted().is_file()
    if token is None:
        assert reply.json()["api_url"] == "https://code.corp.example/api/v4"
        assert read_yaml(_file(client))["accounts"]["corp"] == {
            "forge": "gitlab",
            "web_url": "https://code.corp.example",
            "default_for_host": True,
        }
    assert _file(client).stat().st_mode & 0o777 == 0o600
    assert list(client.get("/api/forges").json()["accounts"]) == ["corp"]
    assert "corp" in client.app.state.forges.accounts
    assert client.delete("/api/forges/corp").status_code == 204
    assert not _pasted().exists()
    assert elsewhere.exists()
    assert "corp" not in client.app.state.forges.accounts
    assert client.get("/api/forges").json() == {"accounts": {}}
    assert client.delete("/api/forges/corp").status_code == 404


@pytest.mark.parametrize(
    ("second", "status", "text"),
    [
        (GL, 409, "already exists"),
        ({**GL, "name": "plain", "web_url": "http://code.corp.example"}, 422, "https"),
        (
            {**GL, "name": "other", "default_for_host": True},
            422,
            "default_for_host",
        ),
        ({**GL, "name": "Bad Name"}, 422, "name"),
        (
            {**API, "name": "cmd", "token": COMMAND},
            403,
            "runs code on the server's machine",
        ),
        ({**GL, "name": "plain", "token": {"from": "env", "env": "X_Y"}}, 422, "carries no token"),
        (
            {**GL, "name": "port", "api_url": "https://code.corp.example:8443/api/v4"},
            422,
            "must be web_url's origin",
        ),
        (
            {**API, "name": "envy", "token": {"from": "env", "env": "CORP_GL"}},
            422,
            "env_passthrough names CORP_GL",
        ),
        ({**API, "name": "str", "token": TOK}, 422, "token: must be an object"),
        ({**API, "name": "top", TOK: "x"}, 422, "the body has a key forges.yaml"),
        ({**API, "name": "top", "value": TOK, "kind": "pat"}, 422, "the body has a key"),
        (TOK, 422, "the body must be a JSON object"),
        ({**API, "name": "key", "token": {TOK: "x"}}, 422, "token has a key forges.yaml"),
        (
            {**API, "name": "name", "token": {"from": "env", "env": TOK}},
            422,
            "looks like a token",
        ),
    ],
    ids=[
        "duplicate-409",
        "http-422",
        "second-default-422",
        "bad-name-422",
        "command-source-403",
        "cli-with-token-422",
        "api-url-other-port-422",
        "env-source-a-repo-passes-through-422",
        "token-as-a-string-never-echoed",
        "extra-top-level-key-is-the-name",
        "extra-top-level-key-is-the-value",
        "body-is-a-string",
        "token-key-is-the-value",
        "token-pasted-as-a-variable-name",
    ],
)
def test_a_refused_add_writes_nothing(client, second, status, text):
    client.post("/api/forges", json={**GL, "default_for_host": True})
    (client.app.state.templates_dir / "repos.yaml").write_text(
        "repos:\n  - {path: /r, forge: gitlab, env_passthrough: [CORP_GL]}\n"
    )
    before = _file(client).read_text()
    reply = client.post("/api/forges", json=second)
    assert reply.status_code == status and text in reply.text
    assert TOK not in reply.text  # a refusal never quotes what it refused
    assert _file(client).read_text() == before
    assert client.app.state.forges.accounts.keys() == {"corp"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/forges", {**GL, "name": "other"}),
        ("delete", "/api/forges/corp", None),
        ("patch", "/api/forges/corp", {"default_for_host": True}),
        ("put", "/api/forges/corp/token", {"value": TOK, "kind": "pat"}),
    ],
    ids=["add", "remove", "edit", "token"],
)
def test_an_edit_not_applied_is_never_saved_over(client, method, path, body):
    client.post("/api/forges", json=GL)
    _file(client).write_text("accounts: {}\n")  # a hand edit no reload has taken
    reply = client.request(method.upper(), path, json=body)
    assert reply.status_code == 409 and "not applied" in reply.text
    assert _file(client).read_text() == "accounts: {}\n"
    assert "corp" in client.app.state.forges.accounts
    assert not _pasted().exists()


def test_remove_of_an_account_a_repo_names_is_409_listing_it(client, repo):
    client.post("/api/forges", json=GL)
    connect_repo(repo, forge_account="corp")
    reply = client.delete("/api/forges/corp")
    assert reply.status_code == 409
    assert reply.json()["repos"] == [str(repo.resolve())]
    assert "corp" in read_yaml(_file(client))["accounts"]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/forges", None),
        ("post", "/api/forges", GL),
        ("delete", "/api/forges/x", None),
        ("post", "/api/forges/x/test", None),
        ("patch", "/api/forges/x", {"api_url": "https://code.corp.example/api/v4"}),
        ("put", "/api/forges/x/token", {"value": TOK, "kind": "pat"}),
    ],
    ids=["list", "add", "remove", "test", "edit", "token"],
)
def test_a_kraft_session_is_refused(client, method, path, body):
    reply = getattr(client, method)(
        path, headers={"X-Kraft-Session-Id": "w1/s1"}, **({"json": body} if body else {})
    )
    assert reply.status_code == 403


def _done(code, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


@pytest.mark.parametrize(
    ("forge", "answer", "argv", "ok", "text"),
    [
        (
            "github",
            _done(0, err="Logged in to github.com"),
            ["gh", "auth", "status", "-h", "code.corp.example"],
            True,
            "Logged in",
        ),
        (
            "gitlab",
            _done(1, out="x", err="not logged in"),
            ["glab", "auth", "status", "--hostname", "code.corp.example"],
            False,
            "xnot logged in",
        ),
        (
            "gitlab",
            CliUnavailable("raised-text"),
            None,
            False,
            "not installed",
        ),
        ("github", ForgeError("raised-text"), None, False, "timed out"),
    ],
    ids=["gh-ok", "glab-failed", "cli-missing", "timeout"],
)
def test_test_runs_the_cli_auth_status_for_the_host(
    client, monkeypatch, forge, answer, argv, ok, text
):
    calls = []

    async def run_cli(repo, args, **kw):
        calls.append(args)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(forge_git, "run_cli", run_cli)
    client.post("/api/forges", json={**GL, "forge": forge})
    body = client.post("/api/forges/corp/test").json()
    assert body["ok"] is ok and text in body["output"]
    assert "raised-text" not in body["output"]  # an error's own text never reaches the answer
    assert calls == ([argv] if argv else calls) and len(calls) == 1
    assert client.post("/api/forges/nope/test").status_code == 404


@pytest.mark.parametrize(
    ("break_file", "status"),
    [("repos", 409), ("forges", 200)],
    ids=["broken-repos", "broken-forges"],
)
def test_an_unreadable_file_never_lets_remove_through_and_never_hides_the_running_accounts(
    client, break_file, status
):
    client.post("/api/forges", json=GL)
    templates = client.app.state.templates_dir
    if break_file == "repos":
        (templates / "repos.yaml").write_text("repos: [not: closed")
        assert client.delete("/api/forges/corp").status_code == status
        assert "corp" in read_yaml(_file(client))["accounts"]
    else:
        _file(client).write_text("accounts: [not: closed")
        reply = client.get("/api/forges")
        assert reply.status_code == status and list(reply.json()["accounts"]) == ["corp"]


@pytest.mark.parametrize(
    ("write", "old_mismatch"),
    [
        ("add", False),
        ("remove", False),
        ("add", True),
        ("remove", True),
        ("edit", False),
        ("token", False),
        ("edit", True),
        ("token", True),
    ],
    ids=[
        "add",
        "remove",
        "add-old-mismatch-stands",
        "remove-old-mismatch-stands",
        "edit",
        "token",
        "edit-old-mismatch-stands",
        "token-old-mismatch-stands",
    ],
)
def test_a_write_is_applied_and_clears_a_stale_invalid_mark(client, write, old_mismatch):
    client.post("/api/forges", json={**API, "name": "other", "web_url": "https://o.example"})
    if old_mismatch:
        (client.app.state.templates_dir / "repos.yaml").write_text(
            "repos:\n  - {path: /r, forge: github, forge_account: ghost}\n"
        )
    client.app.state.invalid_forges = "stale"
    reply = {
        "add": lambda: client.post("/api/forges", json=GL),
        "remove": lambda: client.delete("/api/forges/other"),
        "edit": lambda: client.patch(
            "/api/forges/other", json={"api_url": "https://o.example/api/v5"}
        ),
        "token": lambda: _put(client, TOK, "other"),
    }[write]()
    assert reply.status_code in (200, 201, 204), reply.text
    invalid = client.app.state.invalid_forges
    # The old problem is still named, not forgotten; the stale mark is gone.
    assert (invalid is not None and "'ghost' names no account" in invalid) is old_mismatch
    assert old_mismatch or invalid is None
    pending = client.get("/api/apply").json()
    assert "forges.yaml" not in str(pending)


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/forges"), ("post", "/api/forges/corp/test")],
    ids=["list", "test"],
)
def test_a_forges_yaml_unreadable_since_startup_answers_409_naming_it(client, method, path):
    _file(client).write_text("accounts: [not: closed")
    client.app.state.forges = None
    reply = getattr(client, method)(path)
    assert reply.status_code == 409 and "fix the file first" in reply.json()["detail"]


def test_a_refused_add_names_the_field(client):
    reply = client.post("/api/forges", json={**GL, "forge": "bitbucket"})
    assert reply.status_code == 422 and "forge:" in reply.text


def test_the_403_names_forge_accounts(client):
    reply = client.get("/api/forges", headers={"X-Kraft-Session-Id": "w1/s1"})
    assert "forge accounts" in reply.text


_API = (
    "accounts:\n  corp:\n    forge: gitlab\n    client: api\n"
    "    web_url: https://code.corp.example\n    api_url: {api}\n"
    "    token: {{from: env, env: CORP_GL, kind: pat}}\n"
)


def _api_account(client, api="https://code.corp.example/api/v4"):
    _file(client).write_text(_API.format(api=api))
    assert client.post("/api/apply/reload").status_code == 200


def test_an_api_account_lists_its_source_and_probe(client):
    _api_account(client)
    row = client.get("/api/forges").json()["accounts"]["corp"]
    assert row["token"] == {"from": "env", "env": "CORP_GL", "kind": "pat"}
    assert row["probe"] is None or row["probe"]["state"] == "ok"
    assert "probe" in row


def test_test_probes_an_api_account_and_never_runs_the_cli(client, monkeypatch):
    async def no_cli(*a, **kw):
        raise AssertionError("an API account's test ran the forge CLI")

    monkeypatch.setattr(forge_git, "run_cli", no_cli)
    _api_account(client)
    body = client.post("/api/forges/corp/test").json()
    assert body["ok"] is True and "signed in as canned" in body["output"]
    assert body["probe"]["state"] == "ok"
    assert client.get("/api/forges").json()["accounts"]["corp"]["probe"]["state"] == "ok"


def test_test_probes_the_file_as_written(client, monkeypatch):
    _api_account(client)
    seen = []

    async def probe_account(name, account, **kw):
        seen.append(account.api())
        return await real(name, account, **kw)

    real = probe.probe_account
    monkeypatch.setattr(probe, "probe_account", probe_account)
    _file(client).write_text(_API.format(api="https://code.corp.example/api/v5"))  # not reloaded
    client.post("/api/forges/corp/test")
    assert "https://code.corp.example/api/v5" in seen  # the reload's own pass may add v4


def _put(client, value, name="corp", kind="pat", **kw):
    return client.put(f"/api/forges/{name}/token", json={"value": value, "kind": kind}, **kw)


_ACCT = {"forge": "gitlab", "web_url": "https://code.corp.example", "client": "api"}


@pytest.mark.parametrize(
    ("name", "patch", "status", "changes"),
    [
        ("corp", {"api_url": "https://code.corp.example/api/v5"}, 200, {"api_url": "*"}),
        ("corp", {"ca_file": "/etc/other-ca.pem"}, 200, {"ca_file": "*"}),
        ("corp", {"client": "cli"}, 200, {"client": "cli", "ca_file": None, "token": None}),
        ("corp", {"token": COMMAND}, 403, None),
        ("corp", {"token": TOK}, 422, None),
        ("corp", {"token": {TOK: "x"}}, 422, None),
        ("corp", {"value": TOK, "kind": "pat"}, 422, None),
        ("corp", {TOK: "x"}, 422, None),
        ("corp", TOK, 422, None),
        ("corp", {"api_url": "https://code.corp.example:8443/api/v4"}, 422, None),
        ("nope", {"api_url": "https://code.corp.example/api/v5"}, 404, None),
        ("corp", {"api_url": "https://code.corp.example/" + "a" * 70000}, 422, None),
    ],
    ids=[
        "api-url",
        "ca-file",
        "client-to-cli-drops-token",
        "command-403",
        "token-as-a-string-never-echoed",
        "token-key-is-the-value",
        "extra-top-level-key-is-the-value",
        "extra-top-level-key-is-the-name",
        "body-is-a-string",
        "api-url-other-port-422",
        "unknown-404",
        "body-over-64-kib",
    ],
)
def test_edit_changes_only_the_fields_given(client, name, patch, status, changes):
    base = {
        **_ACCT,
        "ca_file": "/etc/ca.pem",
        "token": {"from": "env", "env": "CORP_GL", "kind": "pat"},
    }
    assert client.post("/api/forges", json={"name": "corp", **base}).status_code == 201
    before = read_yaml(_file(client))["accounts"]["corp"]
    reply = client.patch(f"/api/forges/{name}", json=patch)
    assert reply.status_code == status and TOK not in reply.text
    after = read_yaml(_file(client))["accounts"]["corp"]
    if changes is None:
        assert after == before
        return
    expected = {**before, **{k: patch.get(k) if v == "*" else v for k, v in changes.items()}}
    assert after == {k: v for k, v in expected.items() if v is not None and v != "cli"}
    assert client.app.state.forges.accounts["corp"].client == expected.get("client", "api")


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("put", "/token", {"value": TOK, "kind": "pat"}, 403),
        ("patch", "", {"token": {"from": "env", "env": "OTHER_GL", "kind": "pat"}}, 403),
        ("patch", "", {"token": None}, 403),
        ("patch", "", {"client": "cli"}, 403),
        ("patch", "", {"default_for_host": True}, 200),
    ],
    ids=["put-token", "patch-token", "patch-token-null", "patch-client-cli", "patch-other-field"],
)
def test_a_command_token_is_not_replaced_from_the_browser(client, method, path, body, status):
    _file(client).write_text(yaml.safe_dump({"accounts": {"corp": {**_ACCT, "token": COMMAND}}}))
    assert client.post("/api/apply/reload").status_code == 200
    reply = getattr(client, method)(f"/api/forges/corp{path}", json=body)
    assert reply.status_code == status and TOK not in reply.text
    assert status == 200 or "echo x" not in reply.text  # a row shows its own source
    assert status == 200 or "kraft admin forge edit NAME --token-from" in reply.text
    kept = read_yaml(_file(client))["accounts"]["corp"]["token"]
    assert kept == COMMAND


@pytest.mark.parametrize(
    ("patch", "survives"),
    [
        ({"api_url": "https://code.corp.example/api/v5"}, True),
        ({"client": "cli"}, False),
        ({"token": {"from": "env", "env": "CORP_GL"}}, False),
    ],
    ids=["unrelated-edit-keeps-it", "client-cli-deletes-it", "other-source-deletes-it"],
)
def test_an_edit_that_moves_off_the_pasted_file_deletes_it(client, patch, survives):
    client.post("/api/forges", json=API)
    assert _put(client, TOK).status_code == 200
    assert client.patch("/api/forges/corp", json=patch).status_code == 200
    assert _pasted().exists() is survives


@pytest.mark.parametrize(
    ("raw", "headers", "account", "status", "pastes"),
    [
        ({"value": TOK, "kind": "pat"}, {}, API, 200, 1),
        ({"value": TOK, "kind": "pat"}, {}, API, 200, 2),
        ({"value": TOK}, {}, API, 422, 1),
        ('{"value": "' + TOK, {}, API, 422, 1),
        ({"value": [TOK], "kind": "pat"}, {}, API, 422, 1),
        ({"value": TOK, "kind": TOK}, {}, API, 422, 1),
        ({"value": "short", "kind": "pat"}, {}, API, 422, 1),
        ({"value": TOK + " x", "kind": "pat"}, {}, API, 422, 1),
        ({"value": TOK, "kind": "pat"}, {"X-Kraft-Session-Id": "w1/s1"}, API, 403, 1),
        ({"value": TOK, "kind": "pat"}, {}, GL, 409, 1),
        ({"value": "a" * 70000, "kind": "pat"}, {}, API, 422, 1),
        ("[" * 50000, {}, API, 422, 1),
    ],
    ids=[
        "pasted",
        "pasted-twice",
        "missing-kind",
        "not-json",
        "value-not-a-string",
        "kind-is-the-value",
        "too-short",
        "has-whitespace",
        "session-403",
        "cli-account-409",
        "body-over-64-kib",
        "deeply-nested",
    ],
)
def test_a_pasted_token_is_written_private_and_never_echoed(
    client, monkeypatch, raw, headers, account, status, pastes
):
    monkeypatch.setattr(redact, "_secrets", ())
    kicked = []
    monkeypatch.setattr(probe, "kick", lambda st, names=None: kicked.append(list(names)))
    client.post("/api/forges", json=account)
    kicked.clear()
    before = _file(client).read_text()
    content = raw if isinstance(raw, str) else json.dumps(raw)
    for _ in range(pastes):  # the second leaves the account equal: it is probed again anyway
        reply = client.put(
            "/api/forges/corp/token",
            content=content,
            headers={"content-type": "application/json", **headers},
        )
    assert reply.status_code == status, reply.text
    if status != 200:
        assert TOK not in reply.text
        assert not _pasted().exists() and _file(client).read_text() == before
        return
    path = _pasted()
    assert path.read_text().strip() == TOK
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    text = _file(client).read_text()
    assert str(path) in text and TOK not in text and os.path.isabs(str(path))
    assert TOK not in reply.text and reply.json()["token"] == {
        "from": "file",
        "file": str(path),
        "kind": "pat",
    }
    assert path in credentials.secret_files()  # in force now, not at the next reload
    assert kicked == [["corp"]] * pastes
    assert (
        redact.redact(TOK) == "<redacted>"
        and redact.redact(credentials.basic_form(TOK, "pat")) == "<redacted>"
    )


@pytest.mark.parametrize(
    ("method", "body", "protected"),
    [
        ("post", {**API, "token": {"from": "env", "env": "CORP_GL"}}, "env"),
        ("post", {**API, "token": {"from": "file", "file": "{file}"}}, "file"),
        ("patch", {"token": {"from": "file", "file": "{file}"}}, "file"),
        ("patch", {"token": {"from": "env", "env": "CORP_GL"}}, "env"),
    ],
    ids=["add-env", "add-file", "edit-file", "edit-env"],
)
def test_a_new_source_is_protected_at_once(client, monkeypatch, tmp_path, method, body, protected):
    monkeypatch.setenv("CORP_GL", TOK)
    file = tmp_path / "tok"
    kicked = []
    monkeypatch.setattr(probe, "kick", lambda st, names=None: kicked.append(list(names)))
    if method == "patch":
        client.post("/api/forges", json={**API, "token": None})
    kicked.clear()
    body = {**body, "token": {**body["token"], "file": str(file)}} if protected == "file" else body
    url = "/api/forges" + ("/corp" if method == "patch" else "")
    reply = client.request(method.upper(), url, json=body)
    assert reply.status_code in (200, 201), reply.text
    assert kicked == [["corp"]]  # named, not guessed from what changed
    if protected == "env":
        assert "CORP_GL" not in os.environ and credentials.env_token("CORP_GL") == TOK
    else:
        assert Path(os.path.realpath(file)) in credentials.secret_files()


def _hand_accounts(client, **files):
    accounts = {
        name: {
            "forge": "gitlab",
            "client": "api",
            "web_url": "https://code.corp.example",
            "token": {"from": "file", "file": spec, "kind": "pat"},
        }
        for name, spec in files.items()
    }
    _file(client).write_text(yaml.safe_dump({"accounts": accounts}))
    assert client.post("/api/apply/reload").status_code == 200


@pytest.mark.parametrize(
    "how",
    [
        "symlinked-home",
        "dotdot",
        "env-var",
        "link-to-elsewhere",
        "another-account-reads-it",
        "name-outside-the-pattern",
    ],
)
def test_remove_deletes_the_pasted_file_by_its_real_path(client, tmp_path, monkeypatch, how):
    name = "Corp" if how == "name-outside-the-pattern" else "corp"
    mine = _pasted(name)
    mine.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text(TOK)
    if how == "link-to-elsewhere":
        mine.symlink_to(elsewhere)  # Kraft's name is a link: the link goes, not the target
    else:
        mine.write_text(TOK)
    (tmp_path / "homelink").symlink_to(kraft_home())
    monkeypatch.setenv("KH", str(kraft_home()))
    spec = {
        "symlinked-home": str(tmp_path / "homelink" / "secrets" / "corp"),
        "dotdot": f"{mine.parent}/../secrets/corp",
        "env-var": "$KH/secrets/corp",
        "link-to-elsewhere": str(elsewhere),
        "another-account-reads-it": str(mine),
        "name-outside-the-pattern": str(mine),
    }[how]
    _hand_accounts(client, **{name: spec}, **({"other": str(mine)} if "another" in how else {}))
    assert client.delete(f"/api/forges/{name}").status_code == 204
    kept = how in ("another-account-reads-it", "name-outside-the-pattern")
    assert (mine.exists() or mine.is_symlink()) is kept
    assert elsewhere.exists()


@pytest.mark.parametrize("fail", ["validate", "save"])
@pytest.mark.parametrize("first", [False, True], ids=["replacing", "first-paste"])
def test_a_failed_paste_leaves_the_old_token_and_no_orphan(client, monkeypatch, fail, first):
    client.post("/api/forges", json=API)
    if not first:
        assert _put(client, TOK).status_code == 200
    before = _pasted().read_text() if _pasted().exists() else None

    def boom(*a, **kw):
        raise HTTPException(422, "no") if fail == "validate" else OSError("disk")

    if fail == "validate":
        monkeypatch.setattr(routes, "_validated", boom)
    else:
        monkeypatch.setattr(ForgesFile, "save", boom)
    try:
        _put(client, "ghp_FAKESECOND0123456789")
    except OSError:
        assert fail == "save"
    assert (_pasted().read_text() if _pasted().exists() else None) == before
    assert [p.name for p in _pasted().parent.glob("*")] == (["corp"] if before else [])


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    "source",
    [
        {"from": "env", "env": "CORP_GL"},
        {"from": "file", "file": "{shared}"},
        {"from": "file", "file": "{link}"},
        {"from": "file", "file": "{secrets}/someone"},
        {"from": "file", "file": "{cased}/someone"},
        {"from": "file", "file": "{shared_cased}"},
    ],
    ids=[
        "env-another-reads",
        "file-another-reads",
        "link-to-a-file-another-reads",
        "another-name",
        "another-name-in-another-case",
        "file-another-reads-in-another-case",
    ],
)
def test_an_http_write_never_points_an_account_at_a_secret_it_does_not_own(
    client, tmp_path, monkeypatch, method, source
):
    shared = tmp_path / "shared"
    shared.write_text(TOK)
    (tmp_path / "link").symlink_to(shared)
    secrets = kraft_home() / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "someone").write_text(TOK)
    held = {"forge": "gitlab", "client": "api", "web_url": "https://other.example"}
    _file(client).write_text(
        yaml.safe_dump(
            {
                "accounts": {
                    "env-reader": {**held, "token": {"from": "env", "env": "CORP_GL"}},
                    "file-reader": {**held, "token": {"from": "file", "file": str(shared)}},
                }
            }
        )
    )
    assert client.post("/api/apply/reload").status_code == 200
    if method == "patch":
        assert client.post("/api/forges", json=API).status_code == 201
    before = _file(client).read_text()
    fill = {
        "shared": str(shared),
        "link": str(tmp_path / "link"),
        "secrets": str(secrets),
        "cased": str(secrets.parent / "SECRETS"),
        "shared_cased": str(shared).upper(),
    }
    if "cased" in source.get("file", ""):  # a disk that ignores case (macOS): not this one
        monkeypatch.setattr(os.path, "samefile", lambda a, b: str(a).lower() == str(b).lower())
    token = {k: v.format(**fill) for k, v in source.items()}
    reply = (
        client.post("/api/forges", json={**API, "token": token})
        if method == "post"
        else client.patch("/api/forges/corp", json={"token": token})
    )
    assert reply.status_code == 403
    assert "another account" in reply.text
    assert not any(word in reply.text for word in ("CORP_GL", "someone", "shared"))
    assert _file(client).read_text() == before


@pytest.mark.parametrize(
    "own",
    [{"from": "file", "file": "{secrets}/corp"}, {"from": "env", "env": "CORP_GL"}],
    ids=["its-own-pasted-file", "its-own-env"],
)
def test_an_account_may_keep_the_source_it_already_has(client, own):
    own = {k: v.format(secrets=kraft_home() / "secrets") for k, v in own.items()}
    other = {**API, "name": "other", "web_url": "https://other.example"}
    assert client.post("/api/forges", json=other).status_code == 201
    assert client.post("/api/forges", json={**API, "token": own}).status_code == 201
    reply = client.patch("/api/forges/corp", json={"token": own})
    assert reply.status_code == 200, reply.text
    assert client.app.state.forges.accounts["corp"].token.from_ == own["from"]


def test_save_forgets_cached_tokens(client, monkeypatch):
    seeded = {("corp", "{}"): TOK}
    monkeypatch.setattr(credentials, "_TOKENS", seeded)
    assert client.post("/api/forges", json={**API, "token": None}).status_code in (200, 201)
    assert seeded == {}
