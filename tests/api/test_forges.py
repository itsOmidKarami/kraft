"""`/api/forges`: naming forge accounts, and asking whether their CLI is signed in."""

from __future__ import annotations

import subprocess

import pytest
from support.harness import connect_repo

from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError
from kraft.config import read_yaml

GL = {"name": "corp", "forge": "gitlab", "web_url": "https://code.corp.example/"}


def _file(client):
    return client.app.state.templates_dir / "forges.yaml"


def test_add_list_remove_round_trip(client):
    reply = client.post("/api/forges", json={**GL, "default_for_host": True})
    assert reply.status_code == 201, reply.text
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
    ],
    ids=["duplicate-409", "http-422", "second-default-422", "bad-name-422"],
)
def test_a_refused_add_writes_nothing(client, second, status, text):
    client.post("/api/forges", json={**GL, "default_for_host": True})
    before = _file(client).read_text()
    reply = client.post("/api/forges", json=second)
    assert reply.status_code == status and text in reply.text
    assert _file(client).read_text() == before


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("post", "/api/forges", {**GL, "name": "other"}), ("delete", "/api/forges/corp", None)],
    ids=["add", "remove"],
)
def test_an_edit_not_applied_is_never_saved_over(client, method, path, body):
    client.post("/api/forges", json=GL)
    _file(client).write_text("accounts: {}\n")  # a hand edit no reload has taken
    reply = client.request(method.upper(), path, json=body)
    assert reply.status_code == 409 and "not applied" in reply.text
    assert _file(client).read_text() == "accounts: {}\n"
    assert "corp" in client.app.state.forges.accounts


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
    ],
    ids=["list", "add", "remove", "test"],
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
    [("add", False), ("remove", False), ("add", True), ("remove", True)],
    ids=["add", "remove", "add-old-mismatch-stands", "remove-old-mismatch-stands"],
)
def test_a_write_is_applied_and_clears_a_stale_invalid_mark(client, write, old_mismatch):
    client.post("/api/forges", json={**GL, "name": "other", "web_url": "https://o.example"})
    if old_mismatch:
        (client.app.state.templates_dir / "repos.yaml").write_text(
            "repos:\n  - {path: /r, forge: github, forge_account: ghost}\n"
        )
    client.app.state.invalid_forges = "stale"
    if write == "add":
        client.post("/api/forges", json=GL)
    else:
        client.delete("/api/forges/other")
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
